from inspect_sources import DATA, get


PAGES = {
    "communities.html": "/communities",
    "community-detail.html": (
        "/communities/bellhaven-meadows-of-findlay"
    ),
    "about.html": "/about",
}


def main():
    for filename, url in PAGES.items():
        print(f"Reading {url}...", flush=True)

        response = get(url)
        destination = DATA / filename
        destination.write_text(response.text, encoding="utf-8")

        print(f"Saved {destination}")


if __name__ == "__main__":
    main()