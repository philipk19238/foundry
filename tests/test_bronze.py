"""Bronze is meant to be passed around, so nothing that signs in to a portal may reach it: not the session's cookies
and headers, not a saved credential, wherever in the exchange it shows. And a crawl that carries on must not write a
body twice (it would be uploaded twice) or choke on the line a killed attempt was halfway through."""

import asyncio
import datetime as dt
from pathlib import Path

from scrapy.http import HtmlResponse, Request
from scrapy.statscollectors import MemoryStatsCollector

from foundry.core.bronze import HIDDEN, BronzeMiddleware, Secrets
from foundry.core.har import HarEntry
from foundry.core.models import Cookie, Session

API_KEY = "key-0123456789abcdef"
SESSION_TOKEN = "tok-fedcba9876543210"
COOKIE = "sid-aaaabbbbccccdddd"
SESSION = Session(
    cookies=(
        Cookie(name="sid", value=COOKIE, domain="portal.test"),
        Cookie(name="seen", value="1", domain="portal.test"),
    ),
    headers={"X-Auth-Token": SESSION_TOKEN},
    local_storage={"https://portal.test": {"lastPage": "https://portal.test/bids", "path": "/bids/open-solicitations"}},
    captured_at=dt.datetime(2026, 10, 22, tzinfo=dt.UTC),
)


class Stats(MemoryStatsCollector):
    def __init__(self) -> None:
        self._stats = {}


def middleware(folder: Path) -> BronzeMiddleware:
    return BronzeMiddleware(folder, Stats(), Secrets([API_KEY], SESSION))


def exchange(body: str) -> tuple[Request, HtmlResponse]:
    request = Request(
        f"https://portal.test/bids?api_key={API_KEY}&page=1",
        method="POST",
        headers={"X-Auth-Token": SESSION_TOKEN, "Cookie": f"sid={COOKIE}", "Authorization": "Bearer x"},
        body=f"key={API_KEY}",
    )
    response = HtmlResponse(
        request.url, status=200, headers={"Content-Type": "text/html", "Set-Cookie": f"sid={COOKIE}"},
        body=body.encode(), request=request,
    )  # fmt: skip
    return request, response


def test_no_secret_reaches_the_entry_or_the_body(tmp_path: Path) -> None:
    kept = middleware(tmp_path)
    request, response = exchange(f"<p>token {SESSION_TOKEN}, seen 1</p>")
    asyncio.run(kept.process_response(request, response))
    kept.close()
    written = [path.read_text() for path in tmp_path.iterdir()]
    for secret in (API_KEY, SESSION_TOKEN, COOKIE, "Bearer x"):
        assert not any(secret in text for text in written), secret
    entry = HarEntry.model_validate_json((tmp_path / "entries.jsonl").read_text())
    assert entry.request.url.startswith("https://portal.test/bids?")  # a remembered page is no secret
    assert entry.response.content.text == f"<p>token {HIDDEN}, seen 1</p>"
    assert (tmp_path / entry.response.content.sha256).read_text() == entry.response.content.text
    assert {pair.name.lower() for pair in entry.request.headers} == {"x-auth-token"}
    assert entry.response.headers[0].name.lower() == "content-type" and len(entry.response.headers) == 1


def test_a_crawl_that_carries_on_writes_no_body_twice_and_drops_a_half_line(tmp_path: Path) -> None:
    first = middleware(tmp_path)
    request, response = exchange("<p>same</p>")
    asyncio.run(first.process_response(request, response))
    first.close()
    entry = HarEntry.model_validate_json((tmp_path / "entries.jsonl").read_text())
    body = tmp_path / entry.response.content.sha256
    body.unlink()  # uploaded and deleted by the host
    with (tmp_path / "entries.jsonl").open("a") as log:
        log.write('{"startedDateTime": "2026')
    second = middleware(tmp_path)
    asyncio.run(second.process_response(request, response))
    second.close()
    lines = (tmp_path / "entries.jsonl").read_text().splitlines()
    assert [HarEntry.model_validate_json(line).response.content.sha256 for line in lines] == [body.name] * 2
    assert not body.exists()
