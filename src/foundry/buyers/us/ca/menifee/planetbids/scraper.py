import asyncio
import datetime as dt
import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from scrapy import Request, Selector
from scrapy.crawler import Crawler
from scrapy.downloadermiddlewares.retry import get_retry_request
from scrapy.http import Response
from scrapy.settings import BaseSettings

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

API = "https://api-external.prod.planetbids.com/papi/"
PORTAL = "https://vendors.planetbids.com/portal/41576/bo/"
FILES = "https://files-prod01.planetbids.com"
LOCAL = ZoneInfo("America/Los_Angeles")
RELATED = ("bid-downloadable-files", "bid-addenda", "bid-q-and-a-items", "bid-email-history-items")
HEADERS = {
    "Accept": "application/vnd.api+json",
    "Referer": "https://vendors.planetbids.com/",
    "company-id": "41576",
}


class PacedJSONMiddleware:
    """Pace browser fetches too: middleware responses bypass Scrapy's download slots."""

    def __init__(self, crawler: Crawler) -> None:
        self.crawler = crawler
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.replay = bool(crawler.settings.getlist("FOUNDRY_FIXTURES"))

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "PacedJSONMiddleware":
        return cls(crawler)

    async def process_request(self, request: Request) -> None:
        if self.replay:
            return
        async with self.lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, self.next_request - loop.time()))
            self.next_request = loop.time() + 1.5

    def process_response(self, request: Request, response: Response) -> Request | Response:
        if response.status in (401, 403):
            return response  # Let the kit report an actual access refusal.
        reason = None
        if response.status in (202, 408, 429, 500, 502, 503, 504):
            reason = f"PlanetBids HTTP {response.status}"
        elif response.status == 200:
            try:
                json.loads(response.body)
            except (ValueError, UnicodeDecodeError):
                reason = "PlanetBids non-JSON response"
        if reason is None:
            return response
        spider = self.crawler.spider
        assert spider is not None
        retry = get_retry_request(request, spider=spider, reason=reason, max_retry_times=6)
        if retry is not None:
            # Shared cooldown also prevents queued requests prolonging a rate limit.
            retry_after = (response.headers.get("Retry-After") or b"").decode()
            cooldown = min(120, 5 * 2 ** (retry.meta["retry_times"] - 1))
            if retry_after.isdigit():
                cooldown = max(cooldown, int(retry_after))
            if not self.replay:
                self.next_request = max(self.next_request, asyncio.get_running_loop().time() + cooldown)
            return retry
        return response  # Exhaustion must fail visibly, never silently drop a listing.


def api_payload(response: Response) -> dict[str, Any]:
    if response.status != 200:
        raise ValueError(f"PlanetBids API HTTP {response.status} after retries: {response.url}")
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"PlanetBids API returned non-JSON after retries: {response.url}") from error


def list_url(page: int) -> str:
    # These are the exact search defaults of this buyer's visitor page: all stages.
    return (
        API
        + "bids?"
        + urlencode(
            {
                "bid_type_id": 0,
                "cid": 41576,
                "dept_id": 0,
                "due_date_from": "",
                "due_date_to": "",
                "keyword": "",
                "page": page,
                "per_page": 30,
                "sort_by": "",
                "sort_order": -1,
                "stage_id": 0,
            }
        )
    )


def plain(value: str) -> str:
    return " ".join(Selector(text=value).xpath("//text()").getall()).strip()


def file_link(attributes: dict[str, Any]) -> FileLink | None:
    if attributes.get("recalled") or not attributes.get("serverFilename") or not attributes.get("serverFullPath"):
        return None
    # Match the portal's fileHandling.cfm link builder, including its download flag.
    server = urlsplit("https://" + attributes["serverFullPath"].removeprefix("https://").removeprefix("http://"))
    url = (
        f"{FILES}/fileHandling.cfm?path={server.path}"
        f"&filename={quote(attributes['serverFilename'], safe='')}&download=1"
    )
    return FileLink(
        url=url, name=attributes.get("filename") or attributes.get("fileTitle") or attributes["serverFilename"]
    )


class Spider(PortalSpider):
    name = "menifee-planetbids"
    start_urls = [list_url(1)]
    custom_settings = {
        **(PortalSpider.custom_settings or {}),
        "DEFAULT_REQUEST_HEADERS": HEADERS,
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.5,
        "RANDOMIZE_DOWNLOAD_DELAY": False,
        "AUTOTHROTTLE_START_DELAY": 1.5,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1,
        "RETRY_HTTP_CODES": [202, 408, 429, 500, 502, 503, 504, 522, 524],
        # The API's load balancer rejects Scrapy's default user agent.
        "USER_AGENT": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
        ),
    }

    @classmethod
    def update_settings(cls, settings: BaseSettings) -> None:
        super().update_settings(settings)
        middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")
        middlewares[PacedJSONMiddleware] = 560
        settings.set("DOWNLOADER_MIDDLEWARES", middlewares, priority="spider")

    def parse(self, response: Response) -> Iterable[Request]:
        payload = api_payload(response)
        for row in payload["data"]:
            if row["attributes"]["companyId"] != 41576:
                raise ValueError("Search returned an opportunity belonging to another buyer")
            yield Request(API + "bid-details/" + row["id"], headers=HEADERS, callback=self.parse_detail)
        page = int(parse_qs(urlsplit(response.url).query)["page"][0])
        if page < payload["meta"]["totalPages"]:
            yield Request(list_url(page + 1), headers=HEADERS, callback=self.parse)

    def parse_detail(self, response: Response) -> Iterable[Request]:
        detail = api_payload(response)
        if detail["data"]["attributes"]["companyId"] != 41576:
            raise ValueError("Detail belongs to another buyer")
        bid_id = detail["data"]["id"]
        yield Request(
            API + f"{RELATED[0]}?bid_id={bid_id}",
            headers=HEADERS,
            callback=self.parse_related,
            cb_kwargs={"detail": detail, "related": {}, "index": 0},
        )

    def parse_related(
        self, response: Response, detail: dict[str, Any], related: dict[str, Any], index: int
    ) -> Iterable[Request | Listing]:
        related[RELATED[index]] = api_payload(response)
        index += 1
        bid = detail["data"]["attributes"]
        if index < len(RELATED):
            yield Request(
                API + f"{RELATED[index]}?bid_id={bid['bidId']}",
                headers=HEADERS,
                callback=self.parse_related,
                cb_kwargs={"detail": detail, "related": related, "index": index},
            )
            return

        files = {}
        for payload in related.values():
            active = [record for record in payload["data"] if not record.get("attributes", {}).get("recalled")]
            attachment_ids = {
                (ref["type"], ref["id"])
                for record in active
                for relationship in record.get("relationships", {}).values()
                for ref in (relationship.get("data") or [])
            }
            attachments = [
                record for record in payload.get("included", []) if (record["type"], record["id"]) in attachment_ids
            ]
            for record in [*active, *attachments]:
                link = file_link(record.get("attributes", {}))
                if link:
                    files[link.url] = link
        descriptions = [plain(bid.get(key) or "") for key in ("scope", "details", "notes", "minorityReq")]
        yield Listing(
            external_id=str(bid["bidId"]),
            url=PORTAL + f"bo-detail/{bid['bidId']}",
            title=bid["title"],
            buyer="City of Menifee",
            due_at=dt.datetime.fromisoformat(bid["bidDueDate"]).replace(tzinfo=LOCAL)
            if bid.get("bidDueDate")
            else None,
            description="\n\n".join(text for text in descriptions if text),
            metadata={
                **bid,
                "included": detail.get("included", []),
                "relationships": detail["data"].get("relationships", {}),
                "related": {key: {k: v for k, v in value.items() if k != "meta"} for key, value in related.items()},
            },
            files=tuple(files.values()),
        )
