import datetime as dt
import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from pydantic import JsonValue
from scrapy import Request
from scrapy.http import Response

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

BASE = "https://financeonline.austintexas.gov/afo/account_services/solicitation/"
LOCAL = ZoneInfo("America/Chicago")


def text(node) -> str:
    return " ".join(" ".join(node.xpath(".//text()").getall()).split())


class Spider(PortalSpider):
    name = "austin-finance-online"
    start_urls = [BASE + "solicitations.cfm"]

    def parse(self, response: Response) -> Iterable[Request]:
        seen = set()
        for href in response.css('a[href*="solicitation_details.cfm?sid="]::attr(href)').getall():
            url = response.urljoin(href)
            if url not in seen:
                seen.add(url)
                yield Request(url, callback=self.parse_detail)

    def parse_detail(self, response: Response) -> Iterable[Listing]:
        fields = {}
        for row in response.xpath("//tr[th and td]"):
            label = text(row.xpath("./th[1]")).rstrip(":")
            value = text(row.xpath("./td[1]"))
            if label and value:
                fields[label] = value

        due_text = fields.get("Response Due", "")
        match = re.search(r"\d{2}/\d{2}/\d{4},?\s+\d{2}:\d{2}\s+[AP]M", due_text)
        due = (
            dt.datetime.strptime(match[0].replace(",", ""), "%m/%d/%Y %I:%M %p").replace(tzinfo=LOCAL)
            if match
            else None
        )
        files = {}
        for anchor in response.css('a[href*="/financeonline/downloads/"]'):
            url = response.urljoin(anchor.attrib["href"])
            if url not in files:
                files[url] = FileLink(url=url, name=text(anchor) or urlsplit(url).path.rsplit("/", 1)[-1])

        metadata: dict[str, JsonValue] = dict(fields)
        metadata["sid"] = parse_qs(urlsplit(response.url).query)["sid"][0]
        metadata["classification_tables"] = [
            [[text(cell) for cell in row.xpath("./td")] for row in table.xpath(".//tr[td]")]
            for table in response.xpath('//table[.//th[normalize-space(.)="Code"]]')
        ]
        yield Listing(
            external_id=fields["Solicitation Number"],
            url=response.url,
            title=fields["Description"],
            buyer="City of Austin",
            due_at=due,
            description=fields.get("Summary", ""),
            metadata=metadata,
            files=tuple(files.values()),
        )
