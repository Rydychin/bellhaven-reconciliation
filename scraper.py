import hashlib
import json
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from inspect_sources import BASE_URL, DATA, get


def clean(text):
    return " ".join(text.split())


def canonical_url(base, href):
    """Resolve a link, remove fragments, and keep it on our website."""
    url = urljoin(base, href)
    parsed = urlparse(url)
    origin = urlparse(BASE_URL)

    if (parsed.scheme, parsed.netloc) != (
        origin.scheme,
        origin.netloc,
    ):
        return None

    path = parsed.path.rstrip("/") or "/"

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        path,
        "",
        parsed.query,
        "",
    ))


def parse_community(soup, url):
    heading = soup.select_one(".wrap h1")
    detail = soup.select_one("dl.detail")

    if heading is None or detail is None:
        raise ValueError(f"Missing community details: {url}")

    fields = {}

    for label in detail.select("dt"):
        value = label.find_next_sibling("dd")
        if value is not None:
            fields[clean(label.get_text()).lower()] = value

    address = fields.get("address")
    care = fields.get("care offerings")

    if address is None or care is None:
        raise ValueError(f"Missing address or care offerings: {url}")

    # The website separates street and city/state/ZIP using <br>.
    lines = [
        clean(part)
        for part in address.get_text("\n", strip=True).splitlines()
        if clean(part)
    ]

    if len(lines) < 2:
        raise ValueError(f"Unrecognized address layout: {url}")

    city_state_zip = re.fullmatch(
        r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)",
        lines[-1],
    )

    if city_state_zip is None:
        raise ValueError(
            f"Cannot parse city/state/ZIP at {url}: {lines[-1]}"
        )

    offerings = [
        clean(badge.get_text(" ", strip=True))
        for badge in care.select(".badge")
    ]

    if not offerings:
        # Preserve the complete value if the page uses plain text.
        offerings = [clean(care.get_text(" ", strip=True))]

    offerings = sorted(set(value for value in offerings if value))

    if not offerings:
        raise ValueError(f"No care offerings found: {url}")

    phone = fields.get("phone")
    content = soup.select_one(".wrap")

    return {
        "name": clean(heading.get_text(" ", strip=True)),
        "street": ", ".join(lines[:-1]),
        "city": city_state_zip.group(1),
        "state": city_state_zip.group(2),
        "zip": city_state_zip.group(3),
        "care_offerings": offerings,
        "phone": (
            clean(phone.get_text(" ", strip=True))
            if phone is not None
            else ""
        ),
        "source_url": url,
        "notices": [
            clean(notice.get_text(" ", strip=True))
            for notice in soup.select(".notice")
        ],
        "evidence_text": clean(content.get_text(" ", strip=True)),
    }


def scrape():
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%S%fZ")

    evidence_dir = DATA / "website-evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)

    pending = deque([
        BASE_URL + "/",
        BASE_URL + "/about",
        BASE_URL + "/communities",
    ])

    visited = set()
    locations = {}
    discovered_from = {}
    directory_links = set()
    directory_pages = set()
    expected_directory_totals = set()
    expected_page_totals = set()
    homepage_total = None
    manifest = []

    while pending:
        url = pending.popleft()

        if url in visited:
            continue

        if len(visited) >= 200:
            raise ValueError("Unexpectedly large crawl; inspect the links.")

        visited.add(url)
        print(f"Reading {url}", flush=True)

        response = get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        path = urlparse(url).path.rstrip("/") or "/"

        digest = hashlib.sha256(response.content).hexdigest()
        filename = hashlib.sha256(url.encode()).hexdigest()[:20] + ".html"
        evidence_path = evidence_dir / filename
        evidence_path.write_bytes(response.content)

        manifest.append({
            "url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": digest,
            "file": str(evidence_path.relative_to(DATA)),
        })

        if path == "/":
            hero = soup.select_one(".hero")
            if hero is not None:
                match = re.search(
                    r"serve\s+(\d+)\s+communities",
                    hero.get_text(" ", strip=True),
                    re.IGNORECASE,
                )
                if match:
                    homepage_total = int(match.group(1))

        if path == "/communities":
            content = soup.select_one(".wrap")

            if content is None:
                raise ValueError(f"Missing directory content: {url}")

            count = re.search(
                r"Page\s+(\d+)\s+of\s+(\d+)\s*"
                r"·\s*(\d+)\s+communities listed",
                content.get_text(" ", strip=True),
            )

            if count is None:
                raise ValueError(f"Cannot verify directory counts: {url}")

            directory_pages.add(int(count.group(1)))
            expected_page_totals.add(int(count.group(2)))
            expected_directory_totals.add(int(count.group(3)))

            cards = soup.select(".card h3 a[href]")
            if not cards:
                raise ValueError(f"No community cards found: {url}")

            for card in cards:
                target = canonical_url(url, card["href"])
                if target:
                    directory_links.add(target)

        elif path.startswith("/communities/"):
            location = parse_community(soup, url)
            location["evidence_file"] = str(
                evidence_path.relative_to(DATA)
            )
            location["evidence_sha256"] = digest
            locations[url] = location

        # Discover community links on every visited page, including
        # homepage announcements, plus directory pagination.
        for anchor in soup.select("a[href]"):
            target = canonical_url(url, anchor["href"])

            if target is None:
                continue

            target_path = urlparse(target).path

            if (
                target_path == "/communities"
                or target_path.startswith("/communities/")
            ):
                discovered_from.setdefault(target, set()).add(url)

                if target not in visited:
                    pending.append(target)

    if len(expected_page_totals) != 1:
        raise ValueError("Directory page counts are missing or inconsistent.")

    if len(expected_directory_totals) != 1:
        raise ValueError("Directory totals are missing or inconsistent.")

    expected_pages = next(iter(expected_page_totals))
    expected_directory = next(iter(expected_directory_totals))

    if directory_pages != set(range(1, expected_pages + 1)):
        raise ValueError("Not all directory pages were retrieved.")

    if len(directory_links) != expected_directory:
        raise ValueError(
            f"Directory claims {expected_directory} communities, "
            f"but we found {len(directory_links)} unique links."
        )

    missing = directory_links - set(locations)
    if missing:
        raise ValueError(f"Missing detail pages: {sorted(missing)}")

    if homepage_total is not None and len(locations) != homepage_total:
        raise ValueError(
            f"Homepage claims {homepage_total} communities, "
            f"but we found {len(locations)}. Investigate before matching."
        )

    for url, location in locations.items():
        location["discovered_from"] = sorted(
            discovered_from.get(url, set())
        )

    result = {
        "scraped_at": started.isoformat(),
        "total": len(locations),
        "validation": {
            "complete": True,
            "directory_pages": len(directory_pages),
            "directory_expected": expected_directory,
            "directory_found": len(directory_links),
            "homepage_expected": homepage_total,
            "outside_directory": sorted(set(locations) - directory_links),
        },
        "locations": sorted(
            locations.values(),
            key=lambda item: item["name"],
        ),
        "pages": manifest,
    }

    text = json.dumps(result, indent=2, ensure_ascii=False)

    (evidence_dir / "snapshot.json").write_text(
        text,
        encoding="utf-8",
    )

    # Publish the latest result only after every validation succeeds.
    temporary = DATA / "website-locations.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(DATA / "website-locations.json")

    return result


def main():
    result = scrape()

    print("\nSCRAPE COMPLETE")
    print(f"Directory pages: {result['validation']['directory_pages']}")
    print(f"Directory communities: {result['validation']['directory_found']}")
    print(f"Total communities: {result['total']}")

    print("\nCommunities found outside the directory:")
    for url in result["validation"]["outside_directory"]:
        print(f"  {url}")

    print("\nSaved data/website-locations.json")
    print("Saved original HTML under data/website-evidence/")


if __name__ == "__main__":
    main()