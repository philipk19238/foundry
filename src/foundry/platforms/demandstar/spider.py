"""The public opportunities on one DemandStar agency page."""

import datetime as dt
import json
from urllib.parse import urlencode

from scrapy import Request, Selector

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider


class Spider(PortalSpider):
    name = "demandstar"
    api = "https://api.demandstar.com/contents/agency"

    async def start(self):
        yield Request(f"{self.api}/search?{urlencode({'id': self.tenant})}")

    def detail_request(self, endpoint, bid_id, callback, **kwargs):
        # The API accepts this query parameter alongside its POST body. It also
        # gives each bid a distinct fixture URL (replay does not match bodies).
        return Request(
            f"{self.api}/{endpoint}?bidId={bid_id}",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps({"bidId": bid_id, "type": "Bid"}),
            callback=callback,
            cb_kwargs=kwargs,
        )

    def parse(self, response):
        data = response.json()
        rows = data["result"]
        if len(rows) != data["total"]:
            raise ValueError("Agency response is incomplete; recheck public pagination")
        for row in rows:
            yield self.detail_request("summary", row["bidId"], self.parse_detail)

    def parse_detail(self, response):
        bid = response.json()["result"]
        yield self.detail_request("commodityByType", bid["bidID"], self.parse_commodities, bid=bid)

    def parse_commodities(self, response, bid):
        yield self.detail_request(
            "documents", bid["bidID"], self.parse_documents,
            bid=bid, commodities=response.json()["result"],
        )

    def parse_documents(self, response, bid, commodities):
        docs = response.json()["result"]
        raw = bid.get("dueDate")
        due = dt.datetime.fromisoformat(raw) if raw else None
        if due is not None and due.tzinfo is None:
            # DemandStar stores UTC without an offset; verified against Central
            # deadlines displayed by the public detail pages.
            due = due.replace(tzinfo=dt.UTC)
        scope = Selector(text=bid.get("scopeOfWork") or "")
        description = " ".join(" ".join(scope.xpath("//text()").getall()).split())
        yield Listing(
            external_id=str(bid["bidID"]),
            url=f"https://www.demandstar.com/app/limited/bids/{bid['bidID']}/details",
            title=bid["bidName"],
            buyer=bid.get("agencyName"),
            due_at=due,
            description=description,
            metadata={
                **{key: value for key, value in bid.items() if key != "scopeOfWork"},
                "commodities": commodities,
                "documents": docs,
            },
            files=tuple(
                FileLink(
                    url="https://api.demandstar.com/documents/content/v1/document/download?"
                    + urlencode({"docId": doc["bidDocID"], "id": bid["bidID"], "type": "Bid"}),
                    name=doc["fileName"],
                )
                for doc in docs
            ),
        )
