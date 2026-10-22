# Foundry

Agents that open a procurement portal, find its API, and write the scraper that keeps its notices fresh. An agent
works in a Kernel browser and its own clone of this repo, and hands in a pull request; a person reviews and merges it,
and Foundry runs the merged scraper on a schedule. When an agent hits something it can't do (a CAPTCHA, a phone
code, a login wall), it emails a takeover link; a person claims it, works in the agent's browser, and hands it back.
Dev-only demo.

```
POST /api/sources {url}        onboard run: Codex + Kernel browser (stealth, US residential proxy, profile per portal)
                                 in a clone of this repo, on branch agents/<run id>
     stuck? ── request_help ── Knock email ── /help/{id} ── a person takes over the live view, clicks Done
     submit ── every touched site replays its fixtures ── staging crawl of the live portal from the branch
            ── push the branch ── pull request with both ── source is in_review
     a person merges ── review (every 10 min, or the Review button) ── the merge commit is the scraper's version
POST /api/sources/{id}/refresh crawl from main at that commit, in its own process ── download files of new notices
                                 ── store what's new ── judge health
                                 healthy: ready · logged out: relogin run · broken: heal run · 3 repairs: a person
```

## Run it

```bash
mise trust && mise install
just setup             # venv + migrations (SQLite at ./foundry.db)
just dev               # http://localhost:8000, runs are carried out in this process
```

Sign in as the user seeded from `SEED_EMAIL` and `SEED_PASSWORD` in `.env`.

Settings come from `.env` here, then `.env.shared` (a git-ignored symlink to another project's env file with the
OpenAI, Modal and Knock keys), then the process environment. The files win over the environment. Copy
`.env.example` to start; neither file is ever committed.

| Setting | What |
|---|---|
| `KERNEL_API_KEY` | Kernel browsers, profiles and proxies |
| `KNOCK_SECRET_KEY` | must be a development key (`sk_test_`); Foundry refuses anything else |
| `GITHUB_TOKEN` | fine-grained, this repo only: contents and pull requests, read and write |
| `SERVICE_TOKEN`, `CREDENTIALS_KEY` | generated; the second seals portal passwords (Fernet) |
| `DATABASE_URL` | SQLite by default; Postgres (`postgresql+asyncpg://…`) for Modal |
| `DISPATCHER` | `local` (this process) or `modal` (one container per run) |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | for the redeploy function only |

### Knock

One workflow in Knock's **development** environment, key `foundry-help-requested`, with one email step:

```
Subject: An agent needs a hand: {{ reason }}
Body:    {{ reason }}  →  Take over the browser: {{ url }}
```

### Modal (horizontal scale)

`just deploy` deploys `src/foundry/worker/functions.py` to Modal's `dev` environment: `carry_out` runs each job on its
own container (up to `MAX_CONTAINERS`), `refresh_ready` refreshes every healthy source every 6 hours,
`review_pending` picks up merged and closed pull requests every 10 minutes, `redeploy` deploys `main` when a run asks
to restart, and `web` serves this app. It needs a Postgres `DATABASE_URL`; containers can't share a SQLite file.

## Agents change Foundry too

An agent may find that what blocks it is Foundry's own code: a tool it needs, a bug in the core, its instructions. It
fixes that in its clone, opens a pull request with `propose_change`, and asks a person to merge it with
`request_help`. Once it is merged, the agent calls `restart`: the run steps off its source, `redeploy` deploys the
latest `main` on Modal, and the run comes back on the new code in the same Codex thread, with its clone moved onto the
new `main` (uncommitted work kept) and a new browser on the same portal profile. Only merged `main` is ever deployed,
so every change still goes through review. Locally, `restart` carries the run on in this process with the code on
disk; pull and restart `just dev` to run new code.

## Layout

```
src/foundry/
  core/             the kit every spider imports, the same for every portal: models.py (Listing, FileLink, Fixture,
                    Session, Buyer, Site), spider.py (PortalSpider and its middlewares), sites.py (the registry),
                    __main__.py (`python -m foundry.core crawl|check`)
  platforms/        shared platforms, written once, used by many buyers: <platform>/spider.py, fixtures.json
  buyers/           one folder per buyer, by jurisdiction: <country>/<state>/<buyer>/buyer.toml
  app/              the service
    app.py          register(registry) for every service; Jobs (carry out a run) and Refresher (one crawl)
    settings.py, exceptions.py
    core/models.py  Key (content ids) and Lease (a run's fenced hold on a source)
    db/             engine, tables (db/models/), Alembic migrations
    catalog/        sources, buyers, notices, files: content-addressed, insert-only
    auth/           users (argon2), login cookies and API tokens (hashed)
    runs/           runs, the source lease, the event timeline, the dispatch queue
    help/           help requests: the agent's side (HelpDesk) and the people's (HelpQueue); Knock email
    browser/        Kernel browsers, portal accounts and sessions
    repo/           this repo on GitHub: the mirror, run clones and branches, pull requests
    scrapers/       versions (proposed, merged), crawls (own process, from a tree of the repo), downloads, health
    agent/          Codex in a Kernel browser; tools.py is its MCP server; prompts/
  server/           FastAPI: routes/{auth,pages,api}.py, templates/, static/
  worker/           worker.py (dispatchers, the deployer, boot check), functions.py (the Modal app)
formal/             Lean (foundry_rules/), TLA+ (runs/), logic.html (the rules as logic gates)
```

The data schema is four tables: `sources`, `buyers`, `notices` (`notice_id` and `opportunity_id` are SHA-256 hashes
of the content; `metadata` is JSON/JSONB) and `files`, plus `scrapers` (each version and its pull request) and the
app's own (`users`, `runs`, `events`, `help`).

## Adding a buyer or portal

First find out what the portal runs on. The URL and page usually say it: `demandstar.com`, `bidnetdirect.com`,
`procurement.opengov.com`, `bonfirehub.com`, `ionwave.net`, `periscopeholdings.com`, `bidsync.com`.

1. **A platform we already have** (`src/foundry/platforms/<platform>/` exists): add a `[[portals]]` entry with `kind =
   "platform"`, the platform, and this buyer's `tenant`. Capture a listing page and two details for this tenant
   into `src/foundry/buyers/.../<portal>.json`. No code.
2. **A platform we don't have yet**, and other buyers use it too: write `src/foundry/platforms/<platform>/spider.py` so it
   works for any tenant (build every URL from `self.tenant`), with `fixtures.json` from this buyer and a
   `README.md` saying how tenants are named. Then register the buyer as in 1.
3. **A one-off site**, built for this buyer alone: add a `[[portals]]` entry with `kind = "site"`, and write
   `src/foundry/buyers/.../<portal>/scraper.py` and `fixtures.json`.

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

- Import only `scrapy`, `pydantic`, the standard library, and `foundry.core`.
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

from foundry.core.models import FileLink, Listing
from foundry.core.spider import PortalSpider

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

A one-off site looks the same with fixed URLs (`start_urls = [...]`); see `src/foundry/buyers/us/tx/austin/financeonline/`.

## Checking and review

```bash
uv run python -m foundry.core check us/tx/austin/financeonline   # replay one site's fixtures
uv run python -m foundry.core check --changed origin/main        # every site this branch touches
uv run python -m foundry.core check --all                        # every site
```

A check passes when the replay yields listings with no invalid item, crash or lost session. The `check` workflow runs
it on every pull request and on `main`. A pull request an agent opens also carries a staging crawl: one live run of
the branch's spider against the real portal, with sample listings, stored nowhere.

## Verification

```bash
just prove   # Lean: identity, files, ingest, health rules + planted-bug checks (Modal)
just model   # TLA+: the lease, the help handoff, fenced writes (Modal)
just test    # Python vs the Lean model, byte for byte
open formal/logic.html
```
