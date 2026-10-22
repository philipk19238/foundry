import datetime as dt
from urllib.parse import urlencode

from scrapy import Request
from scrapy.http import JsonRequest

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider


class Spider(PortalSpider):
    name = "demandstar"
    tenant = "68ab6996-941d-43f0-bf43-a252f26c653d"
    city_index = "https://www.prcity.com/Bids.aspx"
    api = "https://api.demandstar.com/contents/agency"

    async def start(self):
        yield Request(self.city_index, callback=self.parse_city_index, errback=self.city_unavailable)

    def listing_request(self):
        return Request(f"{self.api}/search?{urlencode({'id': self.tenant})}")

    def city_unavailable(self, failure):
        self.logger.warning("Public city attachment index unavailable: %s", failure.value)
        self.city_bids = {}
        yield self.listing_request()

    def parse_city_index(self, response):
        self.city_bids = {}
        for link in response.css('a[href*="bidID="]'):
            title = " ".join(link.xpath("string(.)").get().split())
            if not title.startswith("Read on"):
                self.city_bids[title.casefold()] = response.urljoin(link.attrib["href"])
        yield self.listing_request()

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
        listing = Listing(
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
                "attachment_access": "DemandStar downloads return HTTP 401 anonymously; public city copies supplemented when available",
            },
        )

        city_url = self.city_bids.get(" ".join(bid["bidName"].split()).casefold())
        if city_url:
            yield Request(
                city_url, callback=self.parse_city_documents,
                cb_kwargs={"listing": listing, "bid_number": bid.get("bidNumber", "")},
                errback=self.city_detail_unavailable,
            )
        else:
            yield listing

    def city_detail_unavailable(self, failure):
        self.logger.warning("Public city bid page unavailable: %s", failure.value)
        yield failure.request.cb_kwargs["listing"]

    def parse_city_documents(self, response, listing, bid_number):
        city_number = response.xpath(
            '//td[normalize-space(.)="Bid Number:"]/following-sibling::td[1]'
        ).xpath("string(.)").get(default="").strip()
        if " ".join(city_number.split()).casefold() != " ".join(bid_number.split()).casefold():
            self.logger.warning("City bid number does not match %s: %s", bid_number, response.url)
            yield listing
            return
        files = tuple(
            FileLink(
                url=response.urljoin(link.attrib["href"]),
                name=" ".join(link.xpath("string(.)").get().split()) + ".pdf",
            )
            for link in response.css(".relatedDocuments a[href]")
            if "/DocumentCenter/View/" in link.attrib["href"]
        )
        yield listing.model_copy(update={
            "files": files,
            "metadata": {
                **listing.metadata,
                "public_document_source": response.url,
                "attachment_gap": "City copies may omit DemandStar plans, specifications or addenda; see document metadata",
            },
        })
