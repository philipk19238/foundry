# Portals

Every procurement portal Foundry scrapes. The folder tree is the registry: nothing is registered anywhere else.

```
portals/core/                 the kit every spider imports; the same for every portal
  models.py                   Listing, FileLink, Fixture, Session, Buyer, Site
  spider.py                   PortalSpider, and the middlewares and pipeline around it
  sites.py                    Sites: the registry read from buyers/
portals/platforms/            shared platforms, written once, used by many buyers
  <platform>/                 spider.py (class Spider(PortalSpider), reads self.tenant), fixtures.json, README.md
portals/buyers/               one folder per buyer, by jurisdiction: <country>/<state>/<buyer>/
  us/tx/austin/
    buyer.toml                the buyer and every portal it posts on
    financeonline/            a one-off site of its own: scraper.py, fixtures.json
    <portal>.json             optional fixtures for one platform tenant
```

## Adding a portal

First find out what the portal runs on. The URL and page usually say it: `demandstar.com`, `bidnetdirect.com`,
`procurement.opengov.com`, `bonfirehub.com`, `ionwave.net`, `periscopeholdings.com`, `bidsync.com`.

1. **A platform we already have** (`portals/platforms/<platform>/` exists): add a `[[portals]]` entry with `kind =
   "platform"`, the platform, and this buyer's `tenant`. Capture a listing page and two details for this tenant
   into `portals/buyers/.../<portal>.json`. No code.
2. **A platform we don't have yet**, and other buyers use it too: write `portals/platforms/<platform>/spider.py` so it
   works for any tenant (build every URL from `self.tenant`), with `fixtures.json` from this buyer and a
   `README.md` saying how tenants are named. Then register the buyer as in 1.
3. **A one-off site**, built for this buyer alone: add a `[[portals]]` entry with `kind = "site"`, and write
   `portals/buyers/.../<portal>/scraper.py` and `fixtures.json`.

Folder names are lowercase, one word where possible: the country code, the two-letter state, then the buyer
(`austin`, `travis-county`, `texas-dot`). A buyer with several portals has one `buyer.toml` listing them all.

```toml
name = "City of Raleigh"
jurisdiction = "Raleigh, North Carolina"

[[portals]]
kind = "platform"
name = "opengov"
platform = "opengov"
tenant = "raleighnc"
url = "https://procurement.opengov.com/portal/raleighnc"
```

## The spider contract

A spider is one module with one `class Spider(PortalSpider)`.

- Import only `scrapy`, the standard library, and `portals.core`.
- `start_urls` are the listing requests (prefer the portal's own JSON API over rendered pages). `parse` yields one
  `Request(..., callback=self.parse_detail)` per opportunity, and the next page back to `parse`.
- `parse_detail` yields one `Listing` per opportunity:
  - `external_id`: the portal's own id (solicitation number, or the record id). It must never change when the
    portal edits the listing.
  - `url` a person can open; `title`; `buyer` (the agency or department, if shown); `due_at`, timezone-aware (the
    portal's local zone if it gives none); `description` as plain text.
  - `metadata`: everything else useful (NAICS, set-aside, contact, status, category) as JSON values.
  - `files`: one `FileLink(url=..., name=...)` per attachment, with a URL that downloads the file itself.
- Don't sign in, send cookies or set auth headers: the kit sends the saved session with every request. If the
  portal signals a lost session other than by a 401/403 or a redirect to a login URL, override `logged_out`.
- Fixtures are a sample: one listing response and two to four detail responses, as
  `[{"method", "url", "status", "headers", "body"}]` with `body` as text. Replay answers the exact URLs in them and
  skips the rest, so request them exactly as captured.

## Example

```python
import datetime as dt
from collections.abc import Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from scrapy import Request
from scrapy.http import Response

from portals.core.models import FileLink, Listing
from portals.core.spider import PortalSpider

LOCAL = ZoneInfo("America/Chicago")


class Spider(PortalSpider):
    name = "examplebids"

    @property
    def api(self) -> str:
        return f"https://{self.tenant}.examplebids.com/api/v2"

    async def start(self):
        yield Request(f"{self.api}/solicitations?{urlencode({'status': 'open', 'page': 1})}")

    def parse(self, response: Response) -> Iterable[Request]:
        page = response.json()
        for row in page["results"]:
            yield Request(f"{self.api}/solicitations/{row['id']}", callback=self.parse_detail)
        if page["page"] < page["pages"]:
            yield Request(f"{self.api}/solicitations?{urlencode({'status': 'open', 'page': page['page'] + 1})}")

    def parse_detail(self, response: Response) -> Iterable[Listing]:
        bid = response.json()
        due = dt.datetime.fromisoformat(bid["closeDate"]).replace(tzinfo=LOCAL) if bid.get("closeDate") else None
        yield Listing(
            external_id=bid["number"],
            url=f"https://{self.tenant}.examplebids.com/solicitations/{bid['id']}",
            title=bid["title"],
            buyer=bid.get("department"),
            due_at=due,
            description=bid.get("summary") or "",
            metadata={"naics": bid.get("naics"), "category": bid.get("category")},
            files=tuple(
                FileLink(url=f"{self.api}/documents/{doc['id']}/download", name=doc["fileName"])
                for doc in bid.get("documents", [])
            ),
        )
```

A one-off site looks the same with fixed URLs (`start_urls = [...]`); see `portals/buyers/us/tx/austin/financeonline/`.

## Checking, committing and review

```bash
uv run python -m portals.core check us/tx/austin/financeonline   # replay one site's fixtures
uv run python -m portals.core check --changed origin/main        # every site this branch touches
```

A check passes when the replay yields listings with no invalid item, crash or lost session. Agents commit on a
branch of their own (`sites/<site key>/<run id>`); Foundry pushes it and opens a pull request with the check and a
staging crawl: one live run of the branch's spider against the real portal, with sample listings, stored nowhere.
The `check` workflow runs the replay on every pull request. A person reviews and merges; on merge, Foundry registers
the merge commit as the site's spider and its scheduled refreshes start.
