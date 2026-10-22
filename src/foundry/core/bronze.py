"""Bronze: everything a portal sent a live crawl, kept as HAR entries and the bodies they name, with what signs in to
the portal hidden so it can be passed around."""

import asyncio
import datetime as dt
import hashlib
import http.client
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured
from scrapy.http import Headers, Request, Response, TextResponse
from scrapy.statscollectors import StatsCollector

from foundry.core.har import HarContent, HarEntry, HarPair, HarPostData, HarRequest, HarResponse, HarTimings
from foundry.core.middlewares import LATENCY, TEXT
from foundry.core.models import Session
from foundry.core.spider import BRONZE, FILE

HIDDEN = "[secret]"
SHORTEST_SECRET = 4
SHORTEST_SESSION_VALUE = 8
URLS = ("http://", "https://", "/")


class Secrets:
    """What signs in to a portal, to replace wherever it shows: its saved credentials and password at any length from
    SHORTEST_SECRET, and the session's cookies, headers and stored values (where tokens live) from
    SHORTEST_SESSION_VALUE, so a short cookie like `1` or `true` doesn't blank out every body, and none that is a URL
    or a path (a page a portal remembers), which would hide the requests themselves. Longest first, so a secret that
    holds another is replaced whole."""

    def __init__(self, named: Iterable[str], session: Session | None) -> None:
        found = {value for value in named if len(value) >= SHORTEST_SECRET}
        if session is not None:
            values = [cookie.value for cookie in session.cookies] + list(session.headers.values())
            for stored in (*session.local_storage.values(), *session.session_storage.values()):
                values += stored.values()
            found |= {value for value in values if len(value) >= SHORTEST_SESSION_VALUE and not value.startswith(URLS)}
        self._values = sorted(found, key=len, reverse=True)

    def hide(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, HIDDEN)
        return text

    def hide_bytes(self, content: bytes) -> bytes:
        for value in self._values:
            content = content.replace(value.encode(), HIDDEN.encode())
        return content


INLINE_BYTES = 256 * 1024
SECRET_REQUEST_HEADERS = {"cookie", "authorization"}
SECRET_RESPONSE_HEADERS = {"set-cookie"}


class BronzeMiddleware:
    """Keeps everything the portal sent, as bronze: one HAR 1.2 entry per response, a JSON line each in the bronze
    folder's `entries.jsonl`, and every body, whatever its size or type, in the folder under its SHA-256, for foundry
    to upload as the crawl goes. A text body up to INLINE_BYTES is also inline in its entry, so the HAR reads in any
    viewer on its own. Attachments are left out (they are kept as files), and so is the session: the request's
    `Cookie` and `Authorization` headers and the response's `Set-Cookie` are dropped, and every other place the
    session's values or the portal's credentials show is hidden (`Secrets`), so a HAR can be passed around without
    what signs in to the portal. It sits closest to the download, so it sees every response before retries and redirects
    act on it: rate limits, and the responses the stealth browser fetched, included. A crawl that carries on doesn't
    write the bodies an earlier attempt wrote again, so none is uploaded twice, and drops the line a killed one was
    halfway through."""

    def __init__(self, folder: Path, stats: StatsCollector, secrets: Secrets) -> None:
        self._folder = folder
        self._stats = stats
        self._secrets = secrets
        log = folder / "entries.jsonl"
        written = log.read_text() if log.exists() else ""
        whole = written[: written.rfind("\n") + 1]
        if whole != written:
            log.write_text(whole)
        self._written = {HarEntry.model_validate_json(line).response.content.sha256 for line in whole.splitlines()}
        self._log = log.open("a")

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "BronzeMiddleware":
        folder = crawler.settings.get("FOUNDRY_BRONZE")
        if not folder:
            raise NotConfigured("no bronze folder for this crawl")
        assert crawler.stats is not None
        raw = crawler.settings.get("FOUNDRY_SESSION")
        session = Session.model_validate_json(raw) if raw else None
        secrets = Secrets(crawler.settings.getdict("FOUNDRY_CREDENTIALS").values(), session)
        middleware = cls(Path(folder), crawler.stats, secrets)
        crawler.signals.connect(middleware.close, signal=signals.spider_closed)
        return middleware

    def close(self) -> None:
        self._log.close()

    async def process_response(self, request: Request, response: Response) -> Response:
        if request.meta.get(FILE):
            return response
        body = self._secrets.hide_bytes(response.body)
        digest = hashlib.sha256(body).hexdigest()
        if digest not in self._written:
            await asyncio.to_thread((self._folder / digest).write_bytes, body)
            self._written.add(digest)
        self._log.write(self.entry(request, response, body, digest).model_dump_json(exclude_none=True) + "\n")
        self._log.flush()
        self._stats.inc_value(BRONZE)
        return response

    def entry(self, request: Request, response: Response, body: bytes, digest: str) -> HarEntry:
        """The exchange as HAR has it, without its secrets (`body` is the response's, hidden already). A body is
        inline only when it is text as sent: a compressed one is kept as its bytes alone."""
        hide = self._secrets.hide
        took = request.meta.get(LATENCY, 0.0)
        kind = (request.headers.get("Content-Type") or b"").decode(errors="replace")
        mime = (response.headers.get("Content-Type") or b"").decode(errors="replace")
        textual = any(part in mime.lower() for part in TEXT) and not response.headers.get("Content-Encoding")
        text = None
        if textual and len(body) <= INLINE_BYTES:
            encoding = response.encoding if isinstance(response, TextResponse) else "utf-8"
            text = body.decode(encoding, errors="replace")
        version = response.protocol or ""
        return HarEntry(
            started_date_time=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=took),
            time=took * 1000,
            request=HarRequest(
                method=request.method,
                url=hide(request.url),
                http_version=version,
                headers=self.pairs(request.headers, SECRET_REQUEST_HEADERS),
                query_string=tuple(
                    HarPair(name=name, value=hide(value))
                    for name, value in parse_qsl(urlsplit(request.url).query, keep_blank_values=True)
                ),
                body_size=len(request.body),
                post_data=HarPostData(mime_type=kind, text=hide(request.body.decode(errors="replace")))
                if request.body else None,
            ),
            response=HarResponse(
                status=response.status,
                status_text=http.client.responses.get(response.status, ""),
                http_version=version,
                headers=self.pairs(response.headers, SECRET_RESPONSE_HEADERS),
                content=HarContent(size=len(body), mime_type=mime, sha256=digest, text=text),
                redirect_url=hide((response.headers.get("Location") or b"").decode(errors="replace")),
                body_size=len(response.body),
            ),
            timings=HarTimings(wait=took * 1000),
        )  # fmt: skip

    def pairs(self, headers: Headers, secret: set[str]) -> tuple[HarPair, ...]:
        return tuple(
            HarPair(name=name.decode(errors="replace"), value=self._secrets.hide(value.decode(errors="replace")))
            for name, values in headers.items() if name.decode(errors="replace").lower() not in secret
            for value in values
        )  # fmt: skip
