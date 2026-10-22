"""Downloads one attachment the way foundry does after a crawl and prints what came back."""

import asyncio
import sys

import httpx

from foundry.app.scrapers.scrapers import BROWSER_HEADERS


async def main(url: str, listing: str) -> None:
    async with httpx.AsyncClient(timeout=60) as http:
        response = await http.get(url, headers={**BROWSER_HEADERS, "Referer": listing}, follow_redirects=True)
    kind = response.headers.get("content-type", "?")
    print(f"{response.status_code} {kind} {len(response.content)} bytes from {response.url}")
    print(repr(response.content[:120]))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
