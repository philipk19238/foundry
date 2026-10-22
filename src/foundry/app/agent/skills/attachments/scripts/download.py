"""Downloads one attachment with a plain GET and prints what came back: a quick look at a link. The live crawl
downloads every file through the stealth browser, and its `files_saved`/`files_failed` are the proof."""

import asyncio
import sys

import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


async def main(url: str, listing: str) -> None:
    async with httpx.AsyncClient(timeout=60) as http:
        response = await http.get(url, headers={**BROWSER_HEADERS, "Referer": listing}, follow_redirects=True)
    kind = response.headers.get("content-type", "?")
    print(f"{response.status_code} {kind} {len(response.content)} bytes from {response.url}")
    print(repr(response.content[:120]))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
