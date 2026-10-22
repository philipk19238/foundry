"""The stress harness's fake portal (`stress/portal.py`), as an agent would write its platform spider: the JSON API
page by page, then each bid's detail with its documents. `tenant` is the portal's host and port on this machine;
never point it at a real portal.

`tuning.json` beside this file, written by the harness for a throughput sweep, holds Scrapy settings to run with:
a real spider would set them in `custom_settings` itself, but a site's arguments carry only its tenant."""

import datetime as dt
import json
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from scrapy import Request
from scrapy.http import Response

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

LOCAL = ZoneInfo("America/Chicago")
TUNING = Path(__file__).with_name("tuning.json")


class Spider(PortalSpider):
    name = "stressbids"
    custom_settings = {**PortalSpider.custom_settings, **(json.loads(TUNING.read_text()) if TUNING.exists() else {})}

    @property
    def api(self) -> str:
        return f"http://{self.tenant}/api"

    async def start(self):
        yield Request(f"{self.api}/bids?{urlencode({'page': 1})}")

    def parse(self, response: Response) -> Iterable[Request]:
        page = response.json()
        for row in page["results"]:
            yield Request(f"{self.api}/bids/{row['id']}", callback=self.parse_detail)
        if page["meta"]["page"] < page["meta"]["pages"]:
            yield Request(f"{self.api}/bids?{urlencode({'page': page['meta']['page'] + 1})}")

    def parse_detail(self, response: Response) -> Iterable[Listing]:
        bid = response.json()
        due = dt.datetime.fromisoformat(bid["closeDate"]).replace(tzinfo=LOCAL) if bid.get("closeDate") else None
        yield Listing(
            external_id=bid["number"],
            url=f"http://{self.tenant}/bids/{bid['id']}",
            title=bid["title"],
            buyer=bid.get("department"),
            due_at=due,
            description=bid.get("summary") or "",
            metadata={"category": bid.get("category")},
            files=tuple(FileLink(url=doc["url"], name=doc["name"]) for doc in bid.get("documents", [])),
        )
