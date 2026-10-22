"""Public CSCR events with the search page's default Posted filter.

InFlight's JSON projects the same PeopleSoft HTML. The source HTML additionally
exposes department IDs for stable event URLs. Actions share server window state,
so finish each event's attachments before reusing its window.
"""

import datetime as dt
import re
from collections import deque
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from scrapy import FormRequest, Request
from scrapy.exceptions import IgnoreRequest

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

HOME = "https://caleprocure.ca.gov/pages/Events-BS3/event-search.aspx"
BASE = "https://caleprocure.ca.gov/psc/psfpd1/SUPPLIER/ERP/c/"
SEARCH = BASE + "AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL"
DETAIL = BASE + "AUC_MANAGE_BIDS.AUC_RESP_INQ_DTL.GBL"
LOCAL = ZoneInfo("America/Los_Angeles")


def text(node, selector):
    return " ".join(node.css(selector).xpath(".//text()").getall()).strip()


def date(value):
    if not value:
        return None
    value = re.sub(r"\s+P[DS]T$", "", " ".join(value.split()))
    fmt = "%Y/%m/%d %I:%M%p" if re.match(r"^\d{4}/", value) else "%m/%d/%Y %I:%M%p"
    return dt.datetime.strptime(value, fmt).replace(tzinfo=LOCAL)


class Spider(PortalSpider):
    name = "california_caleprocure"
    start_urls = [HOME]
    custom_settings = {
        **PortalSpider.custom_settings,
        "CONCURRENT_REQUESTS": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": 0.25,
        "AUTOTHROTTLE_START_DELAY": 0.25,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1,
    }

    def parse(self, response):
        self.events = deque()
        self.seen = set()
        yield Request(SEARCH, callback=self.parse_search)

    def parse_search(self, response):
        rows = response.css('tr[id^="trRESP_INQA_HD_VW_GR$0_row"], tr[id^="trRESP_INQA_HD_VW$0_row"]')
        for row in rows:
            event_id = text(row, 'a[id^="AUC_ID_"]')
            unit = text(row, 'span[id^="RESP_INQA1_WK_BUSINESS_UNIT$"], span[id^="RESP_INQA_HD_VW_BUSINESS_UNIT$"]')
            if not event_id or not unit:
                raise ValueError("Search row missing event or department ID")
            if (unit, event_id) not in self.seen:
                self.seen.add((unit, event_id))
                self.events.append((unit, event_id))
        counter = " ".join(response.css(".PSGRIDCOUNTER::text").getall())
        total = re.search(r"of\s+([\d,]+)", counter)
        if not total:
            raise ValueError("Missing search results count")
        self.crawler.stats.set_value("caleprocure/portal_total", int(total[1].replace(",", "")))
        next_link = response.css('a[id$="$hdown$0"]')
        if next_link:
            yield self.action(response, next_link.attrib["id"], self.parse_search)
            return
        if len(self.events) != int(total[1].replace(",", "")):
            raise ValueError(f"Search returned {len(self.events)} events but reports {total[1]}")
        for window in (3, 4):
            yield from self.next_event(window)

    def detail_failed(self, failure):
        # Replay skips unsampled details; keep both window queues moving.
        if not (failure.check(IgnoreRequest) and self.settings.getlist("FOUNDRY_FIXTURES")):
            failure.raiseException()
        yield from self.next_event(failure.request.cb_kwargs["window"])

    def next_event(self, window):
        if self.events:
            unit, event_id = self.events.popleft()
            query = urlencode(
                {
                    "Page": "AUC_RESP_INQ_DTL",
                    "Action": "U",
                    "AUC_ID": event_id,
                    "AUC_ROUND": "1",
                    "BIDDER_ID": "BID0000001",
                    "BIDDER_LOC": "1",
                    "BIDDER_SETID": "STATE",
                    "BIDDER_TYPE": "B",
                    "BUSINESS_UNIT": unit,
                }
            )
            yield Request(
                DETAIL.replace("/psfpd1/", f"/psfpd1_{window}/") + "?" + query,
                callback=self.parse_detail,
                errback=self.detail_failed,
                cb_kwargs={"unit": unit, "event_id": event_id, "window": window},
            )

    def action(self, response, action, callback, state=None):
        fields = {
            node.attrib["name"]: node.attrib.get("value", "") for node in response.css('input[type="hidden"][name]')
        }
        fields["ICAction"] = action
        parts = urlsplit(response.url)
        query = dict(parse_qsl(parts.query))
        # Tested on the server: the query also distinguishes POST fixtures.
        query["ICAction"] = action
        url = urlunsplit(parts._replace(query=urlencode(query)))
        return FormRequest(
            url,
            formdata=fields,
            callback=callback,
            dont_filter=True,
            cb_kwargs={"state": state} if state is not None else {},
        )

    @staticmethod
    def table(response, prefix):
        rows = []
        for row in response.css(f'tr[id^="tr{prefix}$0_row"]'):
            values = {
                node.attrib["id"].split("$")[0]: " ".join(node.xpath(".//text()").getall()).strip()
                for node in row.css("span[id]")
            }
            if any(values.values()):
                rows.append(values)
        return rows

    @staticmethod
    def conferences(response):
        return [
            {
                node.attrib["id"].split("$")[0]: " ".join(node.xpath(".//text()").getall()).strip()
                for node in table.css('span[id^="ZZ_BID_CNF_VW_"]')
            }
            for table in response.css('table[id^="ACE_ZZ_BID_CNF_VW$"]')
            if text(table, 'span[id^="ZZ_BID_CNF_VW_DATE1$"]')
        ]

    def parse_detail(self, response, unit, event_id, window):
        title = text(response, '[id="AUC_HDR_ZZ_AUC_NAME"]')
        if not title:
            raise ValueError(f"Missing detail for {unit}/{event_id}")
        current = {
            "external_id": f"{unit}/{event_id}",
            "url": f"https://caleprocure.ca.gov/event/{quote(unit)}/{quote(event_id)}",
            "title": title,
            "buyer": text(response, '[id="BUS_UNIT_TBL_FS_DESCR"]'),
            "due_at": date(text(response, '[id="AUC_HDR_AUC_DTTM_FINISH"]')),
            "description": text(response, '[id="AUC_HDR_DESCRLONG"]'),
            "metadata": {
                "event_id": event_id,
                "department_id": unit,
                "status": "Posted",
                "format": text(response, '[id="RESP_AUC_H0B_WK_AUC_FORMAT_BIDBER"]'),
                "type": text(response, '[id="AUC_HDR_AUC_TYPE"]'),
                "version": text(response, '[id="AUC_HDR_AUC_VERSION"]'),
                "published_at": text(response, '[id="AUC_HDR_AUC_DTTM_START"]'),
                "contact_name": text(response, '[id="AUC_HDR_NAME1"]'),
                "contact_phone": text(response, '[id="AUC_HDR_PHONE"]'),
                "contact_email": text(response, '[id="RESP_INQ_DL0_WK_EMAILID"]'),
                "unspsc": self.table(response, "ZZ_UNSPSC_CD_VW2"),
                "service_areas": self.table(response, "ZZ_AUC_SA_TBL"),
                "contractor_licenses": self.table(response, "ZZ_UNSPSC_CD_VW"),
                "pre_bid_conferences": self.conferences(response),
            },
        }
        state = {"current": current, "files": [], "file_actions": deque(), "package_pages": set(), "window": window}
        button = response.css('a[id="RESP_INQ_DL0_WK_AUC_DOWNLOAD_PB"]')
        if button:
            yield self.action(response, button.attrib["id"], self.parse_package, state)
        else:
            yield from self.finish_event(state)

    def parse_package(self, response, state):
        state["current"]["metadata"]["package_comments"] = text(response, "textarea")
        for row in response.css('tr[id^="trAUC_ATTCH_HD_VW$0_row"]'):
            name = text(row, 'span[id^="PV_ATTACH_WRK_ATTACHUSERFILE$"]')
            button = row.css('input[id^="PV_ATTACH_WRK_SCM_DOWNLOAD$"]')
            if name and button:
                state["file_actions"].append((button.attrib["id"], name))
        yield from self.next_file(response, state)

    def next_file(self, response, state):
        if state["file_actions"]:
            action, state["file_name"] = state["file_actions"].popleft()
            yield self.action(response, action, self.parse_file, state)
            return
        next_link = response.css('a[id="AUC_ATTCH_HD_VW$hdown$0"]')
        if next_link:
            counter = text(response, ".PSGRIDCOUNTER")
            if counter in state["package_pages"]:
                raise ValueError("Attachment pagination did not advance")
            state["package_pages"].add(counter)
            yield self.action(response, next_link.attrib["id"], self.parse_package, state)
            return
        yield from self.finish_event(state)

    def parse_file(self, response, state):
        match = re.search(r"window\.open\('([^']+/view/[^']+)'", response.text)
        if not match:
            raise ValueError(f"No download link for {state['file_name']}")
        state["files"].append(FileLink(name=state["file_name"], url=match[1].replace("/view/", "/viewredirect/")))
        yield from self.next_file(response, state)

    def finish_event(self, state):
        yield Listing(**state["current"], files=tuple(state["files"]))
        yield from self.next_event(state["window"])
