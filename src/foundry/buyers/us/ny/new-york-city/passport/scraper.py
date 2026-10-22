"""NYC PASSPort's public RFx page, with its unmodified visitor filters and rounds."""

import datetime as dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from scrapy import FormRequest, Request, Selector
from scrapy.http import Response

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

START = "https://passport.cityofnewyork.us/page.aspx/en/rfp/request_browse_public"
LOCAL = ZoneInfo("America/New_York")
GRID = "body_x_grid_grd"
PREFIX = "body_x_tabc_rfp_ext_prxrfp_ext_x_"


def text(node) -> str:
    return " ".join(" ".join(node.xpath(".//text()").getall()).split())


def next_page(response, grid, callback, **kwargs):
    """Follow the actual next control: the initial row count is only an estimate."""
    index = int(response.css(f'input[id="hdnCurrentPageIndex{grid}"]::attr(value)').get("0"))
    pager = grid.removesuffix("_grd") + "_PagerBtnNextPage"
    button = response.css(f'button[id="{pager}"]')
    if not button or "disabled" in button.attrib.get("class", "").split():
        return None
    target = int(button.attrib["data-page-index"])
    if target <= index:
        raise ValueError(f"Non-advancing pagination for {grid} at {response.url}")
    return page_request(response, grid, target, callback, **kwargs)


def page_request(response, grid, target, callback, **kwargs):
    return FormRequest.from_response(
        response,
        formid="mainForm",
        formdata={"__EVENTTARGET": grid, "__EVENTARGUMENT": f"Page|{target}", "REQUEST_METHOD": "POST"},
        dont_click=True,
        callback=callback,
        cb_kwargs={**kwargs, "expected_grid": grid, "expected_page": target},
    )


def check_page(response, expected_grid, expected_page):
    if expected_grid:
        actual = response.css(f'input[id="hdnCurrentPageIndex{expected_grid}"]::attr(value)').get()
        if actual != str(expected_page):
            raise ValueError(f"Expected {expected_grid} page {expected_page}, received {actual}: {response.url}")


class Spider(PortalSpider):
    name = "nyc-passport-public"
    start_urls = [START]
    custom_settings = {
        **PortalSpider.custom_settings,
        "AUTOTHROTTLE_START_DELAY": 0.1,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 4.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
    }

    def parse(self, response: Response, expected_grid=None, expected_page=0) -> Iterable[Request]:
        check_page(response, expected_grid, expected_page)
        # The public session remembers its last page; always enumerate from page zero.
        if expected_grid is None:
            index = response.css(f'input[id="hdnCurrentPageIndex{GRID}"]::attr(value)').get("0")
            if index != "0":
                yield page_request(response, GRID, 0, self.parse)
                return
        rows = response.css(f"#{GRID} tr[data-id]")
        if not rows:
            raise ValueError(f"Public RFx grid missing or empty at {response.url}")
        for row in rows:
            cells = [text(cell) for cell in row.xpath("./td")]
            href = row.css('a[href*="/bpm/process_manage_extranet/"]::attr(href)').get()
            if not href:
                raise ValueError("RFx row has no public detail URL")
            # The two hidden date columns are internal; use the visible local timestamps.
            summary = dict(
                zip(
                    [
                        "program",
                        "industry",
                        "epin",
                        "title",
                        "agency",
                        "status",
                        "procurement_method",
                        "internal_release",
                        "internal_due",
                        "release_date",
                        "due_date",
                        "remaining",
                        "main_commodity",
                    ],
                    cells[1:],
                    strict=True,
                )
            )
            for key in ("internal_release", "internal_due", "remaining"):
                summary.pop(key)
            summary["record_id"] = row.attrib["data-id"]
            yield Request(response.urljoin(href), callback=self.parse_detail, cb_kwargs={"summary": summary})
        request = next_page(response, GRID, self.parse)
        if request:
            yield request

    def parse_detail(
        self, response: Response, summary: dict, state=None, expected_grid=None, expected_page=0
    ) -> Iterable[Listing | Request]:
        check_page(response, expected_grid, expected_page)
        if state is None:
            # Detail grids also remember the visitor's last document/conference page.
            for grid in response.css("table.iv-grid-view[id]"):
                grid_id = grid.attrib["id"]
                index = response.css(f'input[id="hdnCurrentPageIndex{grid_id}"]::attr(value)').get("0")
                if index != "0":
                    yield page_request(response, grid_id, 0, self.parse_detail, summary=summary)
                    return
            title = response.css(f'input[id="{PREFIX}txtRfpLabel"]::attr(value)').get()
            if not title:
                raise ValueError(f"Public RFx detail missing at {response.url}")
            fields = {}
            for node in response.css(f'input[id^="{PREFIX}"], textarea[id^="{PREFIX}"]'):
                if node.attrib.get("type") == "hidden" and not node.attrib["id"].endswith("time"):
                    continue
                value = node.attrib.get("value") if node.root.tag == "input" else node.xpath("string(.)").get()
                if value and not node.attrib["id"].endswith("_search"):
                    fields[node.attrib["id"].removeprefix(PREFIX)] = value
            description = fields.pop("txtRfpProcess", "")
            description = text(Selector(text=description.replace("<br", " <br"))) if description else ""
            due = summary["due_date"]
            due_at = dt.datetime.strptime(due, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=LOCAL) if due else None
            state = {
                "title": title,
                "description": description,
                "due_at": due_at,
                "fields": fields,
                "files": {},
                "tables": {},
                "completed": [],
            }
        if expected_grid:
            state["completed"].append((expected_grid, expected_page))
        # All public detail grids, including Documents/addenda and pre-bid conferences.
        grids = response.css("table.iv-grid-view[id]")
        for grid in grids:
            grid_id = grid.attrib["id"]
            index = int(response.css(f'input[id="hdnCurrentPageIndex{grid_id}"]::attr(value)').get("0"))
            key = f"{grid_id}:{index}"
            state["tables"][key] = {
                "columns": [text(th) for th in grid.css("thead th")],
                "rows": [[text(td) for td in tr.xpath("./td")] for tr in grid.css("tbody tr")],
            }
            for anchor in grid.css('a[href*="/c/public_document/download"]'):
                url = response.urljoin(anchor.attrib["href"].strip())
                state["files"][url] = text(anchor)
        for grid in grids:
            grid_id = grid.attrib["id"]
            request = next_page(response, grid_id, self.parse_detail, summary=summary, state=state)
            if request:
                target = (grid_id, request.cb_kwargs["expected_page"])
                if target not in state["completed"]:
                    yield request
                    return
        yield Listing(
            external_id=summary["record_id"],
            url=response.url,
            title=state["title"],
            buyer=summary["agency"],
            due_at=state["due_at"],
            description=state["description"],
            metadata={
                **summary,
                "timezone": "America/New_York",
                "fields": state["fields"],
                "tables": state["tables"],
                "round_and_lot": text(response.css("h1.maintitle")),
            },
            files=tuple(FileLink(url=url, name=name) for url, name in state["files"].items()),
        )
