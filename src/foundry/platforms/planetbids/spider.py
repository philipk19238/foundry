import datetime as dt
from urllib.parse import quote
from zoneinfo import ZoneInfo

from scrapy import Request, Selector

from foundry.core.models import Listing, FileLink
from foundry.core.spider import PortalSpider


class Spider(PortalSpider):
    name = "planetbids"
    api = "https://api-external.prod.planetbids.com/papi"

    def request(self, path, callback, **kwargs):
        return Request(
            f"{self.api}/{path}", callback=callback,
            headers={"Origin": "https://vendors.planetbids.com",
                     "Referer": "https://vendors.planetbids.com/",
                     "company-id": self.tenant, "Accept": "application/vnd.api+json"},
            **kwargs,
        )

    async def start(self):
        yield self.request(f"agencies/{self.tenant}", self.parse_agency)

    def parse_agency(self, response):
        agency = response.json()["data"]["attributes"]
        self.buyer_name = agency["companyName"]
        self.local_zone = ZoneInfo(agency["timeZoneName"])
        yield self.list_page(1)

    def list_page(self, page):
        return self.request(
            f"bids?bid_type_id=0&cid={self.tenant}&dept_id=0&due_date_from=&due_date_to="
            f"&keyword=&page={page}&per_page=30&sort_by=&sort_order=-1&stage_id=0",
            self.parse, cb_kwargs={"page": page},
        )

    def parse(self, response, page=1):
        payload = response.json()
        for row in payload["data"]:
            yield self.request(f"bid-details/{row['id']}", self.parse_detail)
        if page < payload["meta"]["totalPages"]:
            yield self.list_page(page + 1)

    def parse_detail(self, response):
        payload = response.json()
        bid = payload["data"]["attributes"]
        yield self.request(
            f"bid-downloadable-files?bid_id={bid['bidId']}", self.parse_files,
            cb_kwargs={"bid": bid, "included": payload.get("included", [])},
        )

    def parse_files(self, response, bid, included):
        files = []
        for row in response.json()["data"]:
            doc = row["attributes"]
            if doc.get("recalled"):
                continue
            path = doc["serverFullPath"].rstrip("/") + "/" + doc["serverFilename"]
            if not path.startswith(("https://", "http://")):
                path = "https://" + path
            files.append(FileLink(url=quote(path, safe=":/%"), name=doc["filename"]))
        parts = []
        for key in ("scope", "details", "notes", "minorityReq"):
            text = " ".join(Selector(text=bid.get(key) or "").xpath("//text()").getall()).strip()
            if text:
                parts.append(text)
        due = bid.get("bidDueDate")
        yield Listing(
            external_id=str(bid["bidId"]),
            url=f"https://vendors.planetbids.com/portal/{self.tenant}/bo/bo-detail/{bid['bidId']}",
            title=bid["title"], buyer=self.buyer_name,
            due_at=dt.datetime.fromisoformat(due).replace(tzinfo=self.local_zone) if due else None,
            description="\n\n".join(parts),
            metadata={
                **{k: v for k, v in bid.items() if k not in ("scope", "details", "notes")},
                "categories": [r["attributes"] for r in included if r["type"] == "categories"],
            },
            files=tuple(files),
        )
