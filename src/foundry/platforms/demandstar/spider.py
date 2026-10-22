import datetime as dt
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import JsonRequest

from foundry.core.models import Listing
from foundry.core.spider import PortalSpider


class Spider(PortalSpider):
    name = "demandstar"
    api = "https://api.demandstar.com/contents/agency"

    async def start(self):
        yield Request(f"{self.api}/search?{urlencode({'id': self.tenant})}")

    def parse(self, response):
        for bid in response.json()["result"]:
            if bid["statusType"] == "AC":
                bid_id = bid["bidId"]
                yield JsonRequest(
                    f"{self.api}/summary?bidId={bid_id}",
                    data={"bidId": bid_id}, callback=self.parse_detail,
                )

    def parse_detail(self, response):
        bid = response.json()["result"]
        if bid.get("visitLimitExceeded"):
            raise ValueError("DemandStar anonymous detail visit limit exceeded")
        yield JsonRequest(
            f"{self.api}/documents?bidId={bid['bidID']}",
            data={"bidId": bid["bidID"]}, callback=self.parse_documents,
            cb_kwargs={"bid": bid},
        )

    def parse_documents(self, response, bid):
        # API dueDate is UTC without a suffix; 22:00 displays as 14:00 Pacific.
        due = dt.datetime.fromisoformat(bid["dueDate"]).replace(tzinfo=dt.timezone.utc) if bid.get("dueDate") else None
        yield Listing(
            external_id=str(bid["bidID"]),
            url=f"https://www.demandstar.com/app/limited/bids/{bid['bidID']}/details",
            title=bid["bidName"], buyer=bid["agencyName"], due_at=due,
            description=(bid.get("scopeOfWork") or "").replace(r"\n", "\n").replace(r'\"', '"'),
            metadata={
                "bid_identifier": bid.get("bidIdentifier"),
                "status": bid.get("bidExternalStatus"),
                "bid_type": bid.get("bidTypeDescription"),
                "bid_writer": bid.get("bidWriter"),
                "broadcast_date": bid.get("broadcastDate"),
                "budget": bid.get("projectEstimatedBudget") if bid.get("displayBudgetToSuppliers") else None,
                "documents": response.json()["result"],
                "attachment_access": "Login required; anonymous metadata only",
            },
        )
