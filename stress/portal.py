"""A fake procurement portal on this machine, for the stress harness only: a JSON listing API with bid details on
one host (127.0.0.1), and the bids' files on a second (localhost, another port), so a crawl meets a cross-site file
host as it does on real portals. Its faults (rate limits, firewall challenges, 5xx, hangs, resets, expiring links,
viewer pages) are set per scenario by `Knobs`, and it keeps a ledger of what it answered."""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import random
import socket
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from types import TracebackType

from aiohttp import web

from stress.models import Knobs, Served

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
HOME = "<!doctype html><title>Stress Test Purchasing</title><h1>Open bids</h1>"
VIEWER = "<!doctype html><title>Document viewer</title><iframe src='/viewer/frame'></iframe>"
SECRET = b"stress-portal-local-only"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Ledger:
    """Every answer the portal gave, by kind of path and status, and each file's bytes sent."""

    def __init__(self) -> None:
        self._responses: Counter[str] = Counter()
        self._sends: Counter[str] = Counter()

    def record(self, kind: str, status: int | str) -> None:
        self._responses[f"{kind} {status}"] += 1

    def sent(self, key: str) -> None:
        self._sends[key] += 1

    @property
    def served(self) -> Served:
        return Served(responses=dict(sorted(self._responses.items())), file_sends=dict(self._sends))


class TokenBucket:
    """`rate` requests a second, up to `burst` at once; a request finding the bucket empty is refused."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._at = time.monotonic()

    def take(self) -> bool:
        if self._rate <= 0:
            return True
        now = time.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._at) * self._rate)
        self._at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


class Signer:
    """Signed file links, as a presigned S3 or CDN URL: valid until `expires`, refused after."""

    def sign(self, path: str, expires: float) -> str:
        return hmac.new(SECRET, f"{path}:{expires:.3f}".encode(), hashlib.sha256).hexdigest()[:24]

    def valid(self, path: str, expires: str, signature: str) -> bool:
        try:
            until = float(expires)
        except ValueError:
            return False
        return hmac.compare_digest(self.sign(path, until), signature) and time.time() < until


class Faults:
    """The API's faults, in the order a real stack meets them: a request that never answers, a dropped connection,
    the rate limiter, the firewall's challenge, then a failing backend."""

    def __init__(self, knobs: Knobs, ledger: Ledger) -> None:
        self._knobs = knobs
        self._ledger = ledger
        self._bucket = TokenBucket(knobs.rate, knobs.burst)
        self._random = random.Random(knobs.seed)

    @web.middleware
    async def middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        kind = "detail" if request.match_info.get("bid") else "page"
        if request.path == "/":
            return await handler(request)
        knobs = self._knobs
        await asyncio.sleep(knobs.latency)
        if self._random.random() < knobs.hangs:
            self._ledger.record(kind, "hang")
            await asyncio.Event().wait()
        if self._random.random() < knobs.resets and request.transport is not None:
            self._ledger.record(kind, "reset")
            request.transport.abort()
            raise web.HTTPServiceUnavailable()
        if not self._bucket.take():
            self._ledger.record(kind, 429)
            headers = {"Retry-After": str(knobs.retry_after)} if knobs.retry_after is not None else {}
            return web.json_response({"error": "too many requests"}, status=429, headers=headers)
        if kind == "detail" and knobs.challenged_bid(int(request.match_info["bid"])):
            self._ledger.record(kind, 202)
            return web.Response(status=202, headers={"x-amzn-waf-action": "challenge"})
        if self._random.random() < knobs.flaky:
            self._ledger.record(kind, 500)
            return web.json_response({"error": "internal"}, status=500)
        response = await handler(request)
        self._ledger.record(kind, response.status)
        return response


class Bids:
    """The portal's content, made up from the knobs and the same on every request: bid `n` is `BID-0000n`, with
    `files_per_listing` documents, each a distinct run of bytes."""

    def __init__(self, knobs: Knobs, signer: Signer, files_url: str) -> None:
        self._knobs = knobs
        self._signer = signer
        self._files_url = files_url

    async def home(self, request: web.Request) -> web.Response:
        return web.Response(text=HOME, content_type="text/html")

    async def page(self, request: web.Request) -> web.Response:
        knobs = self._knobs
        number = int(request.query.get("page", "1"))
        first = (number - 1) * knobs.page_size + 1
        ids = range(first, min(first + knobs.page_size, knobs.listings + 1))
        return web.json_response(
            {"results": [{"id": bid} for bid in ids], "meta": {"page": number, "pages": knobs.pages}}
        )

    async def detail(self, request: web.Request) -> web.Response:
        bid = int(request.match_info["bid"])
        if not 1 <= bid <= self._knobs.listings:
            raise web.HTTPNotFound()
        return web.json_response(
            {
                "id": bid,
                "number": f"BID-{bid:05d}",
                "title": f"Stress test solicitation {bid}",
                "department": "Purchasing",
                "closeDate": f"2027-01-{bid % 28 + 1:02d}T14:00:00",
                "summary": f"Bid {bid}, made up by the stress harness.",
                "category": "Services",
                "documents": [self.document(f"{bid}-{n}") for n in range(self._knobs.files_per_listing)],
            }
        )

    def document(self, key: str) -> dict[str, str]:
        """A file's link, signed now, or already expired for a stale one."""
        path = f"/files/{key}"
        stale = self._knobs.drawn("stale", key, self._knobs.stale)
        expires = time.time() - 1 if stale else time.time() + self._knobs.link_seconds
        query = f"expires={expires:.3f}&sig={self._signer.sign(path, round(expires, 3))}"
        return {"name": f"Document {key}.pdf", "url": f"{self._files_url}{path}?{query}"}


class Files:
    """The file host: a signed link's bytes, a refusal for a bad or expired one, or for a viewer file, a page that
    shows it instead of the file."""

    def __init__(self, knobs: Knobs, signer: Signer, ledger: Ledger) -> None:
        self._knobs = knobs
        self._signer = signer
        self._ledger = ledger

    async def get(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        if not self._signer.valid(request.path, request.query.get("expires", ""), request.query.get("sig", "")):
            self._ledger.record("file", 403)
            return web.Response(status=403, text="<Error><Code>AccessDenied</Code></Error>", content_type="text/xml")
        if self._knobs.drawn("viewer", key, self._knobs.viewers):
            self._ledger.record("file", "viewer")
            return web.Response(text=VIEWER, content_type="text/html")
        self._ledger.record("file", 200)
        self._ledger.sent(key)
        head = f"%PDF-1.7 stress document {key}\n".encode()
        body = head + hashlib.sha256(head).digest() * (max(0, self._knobs.file_bytes - len(head)) // 32)
        return web.Response(body=body, content_type="application/pdf")


class Portal:
    """The fake portal, serving while the context is open: the API at `api_url`, files at `files_url`."""

    def __init__(self, knobs: Knobs) -> None:
        self._knobs = knobs
        self._ledger = Ledger()
        self._api_port = free_port()
        self._files_port = free_port()
        self._runners: list[web.AppRunner] = []

    @property
    def tenant(self) -> str:
        return f"127.0.0.1:{self._api_port}"

    @property
    def api_url(self) -> str:
        return f"http://{self.tenant}/"

    @property
    def files_url(self) -> str:
        return f"http://localhost:{self._files_port}"

    @property
    def served(self) -> Served:
        return self._ledger.served

    async def __aenter__(self) -> "Portal":
        logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
        signer = Signer()
        bids = Bids(self._knobs, signer, self.files_url)
        api = web.Application(middlewares=[Faults(self._knobs, self._ledger).middleware])
        api.add_routes([web.get("/", bids.home), web.get("/api/bids", bids.page), web.get("/api/bids/{bid}", bids.detail)])
        files = web.Application()
        files.add_routes([web.get("/files/{key}", Files(self._knobs, signer, self._ledger).get)])
        for app, host, port in ((api, "127.0.0.1", self._api_port), (files, "localhost", self._files_port)):
            runner = web.AppRunner(app, access_log=None, handler_cancellation=True, shutdown_timeout=1)
            await runner.setup()
            await web.TCPSite(runner, host, port).start()
            self._runners.append(runner)
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        for runner in self._runners:
            with contextlib.suppress(Exception):
                await runner.cleanup()

