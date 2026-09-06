import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("CRM_BASE_URL", "").rstrip("/")
TOKEN = os.getenv("CRM_API_TOKEN", "")

if not BASE_URL or not TOKEN or TOKEN == "PASTE_YOUR_TOKEN_HERE":
    raise SystemExit("Enter the website URL and your actual token in .env.")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "User-Agent": "BellhavenReconciliation/0.1",
})

summary = []


def report(message):
    print(message, flush=True)
    summary.append(message)


def save_json(filename, value):
    (DATA / filename).write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get(path_or_url):
    url = urljoin(BASE_URL + "/", path_or_url)
    base = urlparse(BASE_URL)

    for _ in range(6):
        target = urlparse(url)

        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ValueError("Refusing to send the token to another website.")

        response = session.get(
            url,
            timeout=30,
            allow_redirects=False,
        )
        response.raise_for_status()

        if not response.is_redirect:
            return response

        url = urljoin(url, response.headers["Location"])

    raise ValueError("Too many redirects.")


def inspect_website():
    report("\nReading the website...")

    response = get("/")
    (DATA / "homepage.html").write_text(
        response.text,
        encoding="utf-8",
    )

    soup = BeautifulSoup(response.text, "html.parser")
    links = {}

    for anchor in soup.select("a[href]"):
        url = urljoin(response.url, anchor["href"])

        if urlparse(url).netloc != urlparse(BASE_URL).netloc:
            continue

        if urlparse(url).scheme not in {"http", "https"}:
            continue

        links[url] = anchor.get_text(" ", strip=True)

    save_json("website-links.json", links)
    report("Saved homepage.html and website-links.json")

    for url, label in links.items():
        report(f"  {label or '(no label)'} -> {url}")


def inspect_schema():
    report("\nReading the API documentation...")

    response = get("/api/docs")
    (DATA / "api-docs.html").write_text(
        response.text,
        encoding="utf-8",
    )

    discovered = re.findall(
        r"""(?:url|spec-url)\s*[:=]\s*["']([^"']+)["']""",
        response.text,
    )

    candidates = discovered + [
        "/openapi.json",
        "/api/openapi.json",
        "/api/v1/openapi.json",
    ]

    checked = set()

    for candidate in candidates:
        url = urljoin(response.url, candidate)

        if url in checked:
            continue

        checked.add(url)

        try:
            schema = get(url).json()
        except (requests.RequestException, ValueError):
            continue

        if not isinstance(schema, dict) or "paths" not in schema:
            continue

        save_json("openapi.json", schema)

        account_paths = {
            path: definition
            for path, definition in schema["paths"].items()
            if "account" in path.lower()
        }

        save_json("account-api-schema.json", {
            "openapi": schema.get("openapi"),
            "servers": schema.get("servers", []),
            "paths": account_paths,
            "components": schema.get("components", {}),
        })

        report("Saved openapi.json and account-api-schema.json")

        for path, definition in account_paths.items():
            for method, operation in definition.items():
                if method.lower() in {
                    "get", "post", "put", "patch", "delete"
                }:
                    report(
                        f"  {method.upper()} {path}: "
                        f"{operation.get('summary', '')}"
                    )

        return

    report(
        "Could not find the API schema automatically. "
        "Saved api-docs.html so we can inspect it."
    )


def inspect_accounts():
    report("\nReading a sample of CRM accounts...")

    payload = get("/api/v1/accounts?q=bellhaven").json()
    save_json("bellhaven-search.json", payload)

    report("Saved bellhaven-search.json")

    if isinstance(payload, list):
        report(f"Response contains {len(payload)} items.")

        if payload:
            save_json("sample-account.json", payload[0])
            report("Saved sample-account.json")

    elif isinstance(payload, dict):
        report(f"Response fields: {', '.join(payload.keys())}")

        for key, value in payload.items():
            if isinstance(value, list):
                report(f"List '{key}' contains {len(value)} items.")

                if value:
                    save_json("sample-account.json", value[0])
                    report("Saved sample-account.json")
                    break


def main():
    for operation in [
        inspect_website,
        inspect_schema,
        inspect_accounts,
    ]:
        try:
            operation()
        except (requests.RequestException, ValueError) as error:
            report(f"ERROR: {operation.__name__}: {error}")

    (DATA / "inspection-summary.txt").write_text(
        "\n".join(summary),
        encoding="utf-8",
    )

    report(f"\nFinished. Files are in {DATA}")


if __name__ == "__main__":
    main()