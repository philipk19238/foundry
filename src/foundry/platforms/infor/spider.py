"""Public Infor Supply Management home events and their related records."""

import datetime as dt
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from scrapy import Request, Selector

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider


def values(row):
    return {key: field.get("value") for key, field in row.get("fields", {}).items() if not field.get("isSecured")}


def timestamp(raw):
    # Landmark timestamps are UTC, including the final two fractional digits.
    return dt.datetime.strptime(raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC) if raw else None


def plain(raw):
    return " ".join(Selector(text=raw or "").xpath("//text()").getall()).strip()


class Spider(PortalSpider):
    name = "infor"
    custom_settings = {
        **PortalSpider.custom_settings,
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
    }
    related = (
        ("attachments", "SourcingEventAttachment.ListAttachmentView", "SupplierSourcingEventAttachmentsRel"),
        (
            "questions",
            "SourcingEventQuestion.SupplierQuestionsList",
            "SourcingEventQuestion_BySourcingEvent_UsingSymbolicKey_SetRel",
        ),
        (
            "contacts",
            "SourcingEventContact.ListContactCardList",
            "SourcingEventContact_BySourcingEvent_UsingSymbolicKey_SetRel",
        ),
        ("amendments", "SourcingEventVersion.SupplierAmendmentList", "SourcingEventVersion_PartSetRel"),
        ("lines", "SourcingEventLine.XiSupplierPortalEventLines", "ExecutiveEventLinesRel"),
        ("q_and_a", "SourcingEventQandA.SupplierQAndACardList", "SourcingEventQandARel"),
    )

    @property
    def base(self):
        return self.tenant.split("/page/")[0] + "/"

    @property
    def group(self):
        return parse_qs(urlsplit(self.tenant).query)["csk.SupplierGroup"][0]

    def endpoint(self, path, **params):
        return self.base + path + "?" + urlencode({**params, "csk.SupplierGroup": self.group})

    async def start(self):
        page = urlsplit(self.tenant).path.rsplit("/", 1)[-1]
        yield Request(
            self.endpoint(
                "list/SourcingEvent.HomeEvents", pageop="load", pagesize=10, pagepanel=f"{page}.Main.OpenEvents"
            )
        )

    def parse(self, response):
        data = response.json()["dataViewSet"]
        for row in data["data"]:
            f = values(row)
            resource = row["resourceId"]
            url = self.endpoint(
                "form/" + quote(resource, safe="") + ".XiSupplierPortalSummary", pageop="load", pagesize=1
            )
            yield Request(url, callback=self.parse_detail, cb_kwargs={"resource": resource, "summary": f})
        if data["pagingInfo"]["hasNext"]:
            yield Request(data["pagingUrls"]["nextPageUrl"], callback=self.parse)

    def parse_detail(self, response, resource, summary):
        f = values(response.json())
        state = {"resource": resource, "summary": summary, "detail": f, "records": {}, "files": []}
        yield self.related_request(state, 0)

    def related_request(self, state, index):
        key, view, relation = self.related[index]
        return Request(
            self.endpoint(
                "list/" + view,
                pageop="load",
                pagesize=20,
                dependentList="true",
                relation=state["resource"] + "." + relation,
            ),
            callback=self.parse_related,
            cb_kwargs={"state": state, "index": index},
        )

    def parse_related(self, response, state, index):
        data = response.json()["dataViewSet"]
        key = self.related[index][0]
        rows = state["records"].setdefault(key, [])
        for row in data["data"]:
            fields = values(row)
            rows.append(fields)
            for field_name, field in row.get("fields", {}).items():
                if field_name.endswith("_prd_File") and field.get("value") and not field.get("isSecured"):
                    resource = re.sub(r"\[[^]]*\]", "", row["resourceId"]).replace(":_cv_0", "")
                    suffix = field_name.replace("_prd_", ".")
                    url = self.endpoint("document/" + quote(resource, safe="") + "." + suffix)
                    state["files"].append(FileLink(url=url, name=field.get("linkValue") or field["value"]))
        if data["pagingInfo"]["hasNext"]:
            yield Request(
                data["pagingUrls"]["nextPageUrl"],
                callback=self.parse_related,
                cb_kwargs={"state": state, "index": index},
            )
        elif index + 1 < len(self.related):
            yield self.related_request(state, index + 1)
        else:
            yield self.listing(state)

    def listing(self, state):
        f, summary = state["detail"], state["summary"]
        detail = {k: v for k, v in f.items() if not k.startswith("Display") and not k.startswith("EventResponsesRel")}
        terms = detail.pop("_op_TermsAndConditionsText_spc_translation_cp_", "")
        if terms:
            detail["terms_and_conditions"] = plain(terms)
        return Listing(
            external_id=f"{f['Company']}-{f['SourcingEvent']}",
            url=self.endpoint("form/" + quote(state["resource"], safe="") + ".XiSupplierPortalSummary", action="_open"),
            title=f["Name"].strip(),
            due_at=timestamp(f.get("DerivedCloseDate")),
            description=plain(f.get("Description")),
            metadata={
                "status": summary.get("_op_DerivedStatusForSupplier_spc_translation_cp_"),
                "event": detail,
                **state["records"],
            },
            files=tuple({file.url: file for file in state["files"]}.values()),
        )
