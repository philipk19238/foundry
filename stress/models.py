"""The harness's data: how the fake portal behaves, what a scenario expects, and what one run of it saw."""

import random
import uuid
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field

from foundry.app.scrapers.models.crawl import Crawl, Verdict
from foundry.core.models import CrawlStats
from stress.process import CrawlProcess
from stress.watches import KillingWatch, RecordingWatch, StoppingWatch

KB_PER_MB = 1024


class Knobs(BaseModel, frozen=True):
    """How the fake portal behaves in one scenario. `rate` is the API's token bucket in requests per second (0 for no
    limit), answered past `burst` with a 429 that asks for `retry_after` seconds, or for nothing. The fractions are of
    API requests (`flaky`, `hangs`, `resets`), of bids (`challenged`, the same ones every time, as a firewall that
    flagged them), or of files (`stale`, links issued already expired; `viewers`, files served as an HTML viewer).
    `sessions_last` (0 for forever) is how many API requests the portal's guest sessions last before it forgets them
    all, and sends the next request on one to sign in."""

    listings: int = 1000
    page_size: int = 50
    files_per_listing: int = 3
    file_bytes: int = 20_000
    latency: float = 0.005
    rate: float = 0
    burst: int = 10
    retry_after: int | None = None
    challenged: float = 0
    flaky: float = 0
    hangs: float = 0
    resets: float = 0
    link_seconds: float = 600
    stale: float = 0
    viewers: float = 0
    sessions_last: int = 0
    seed: int = 7

    @property
    def pages(self) -> int:
        return -(-self.listings // self.page_size)

    def drawn(self, kind: str, key: str, fraction: float) -> bool:
        """Whether `key` is one of the `fraction` picked for `kind`: the same answer on every request and every run,
        so the harness knows exactly which bids and files a fault hits."""
        return fraction > 0 and random.Random(f"{self.seed}:{kind}:{key}").random() < fraction

    def challenged_bid(self, bid: int) -> bool:
        return self.drawn("challenge", str(bid), self.challenged)

    def bad_file(self, key: str) -> bool:
        """A file no crawl can get: its link was stale when issued, or it is served as a viewer page."""
        return self.drawn("stale", key, self.stale) or self.drawn("viewer", key, self.viewers)

    def keys(self, bids: list[int]) -> list[str]:
        return [f"{bid}-{n}" for bid in bids for n in range(self.files_per_listing)]

    @property
    def truth(self) -> "Truth":
        reachable = [bid for bid in range(1, self.listings + 1) if not self.challenged_bid(bid)]
        return Truth(
            listings=self.listings,
            reachable=len(reachable),
            files=len(reachable) * self.files_per_listing,
            bad_files=sum(self.bad_file(key) for key in self.keys(reachable)),
        )


class Truth(BaseModel, frozen=True):
    """What the portal holds, worked out from its knobs: the answer a perfect crawl reports. `files` counts those of
    the bids a crawl can reach (not behind a challenge), and `bad_files` those of them no crawl can get."""

    listings: int
    reachable: int
    files: int
    bad_files: int

    @property
    def good_files(self) -> int:
        return self.files - self.bad_files


class Served(BaseModel, frozen=True):
    """What the portal answered: responses by `<kind> <status>` (a hang or a reset as its status), and how many
    times each file's bytes were sent."""

    responses: dict[str, int]
    file_sends: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.responses.values())

    @property
    def refetched(self) -> int:
        """Files whose bytes went out more than once: a crawl downloaded them again."""
        return sum(1 for count in self.file_sends.values() if count > 1)

    def count(self, prefix: str) -> int:
        return sum(count for key, count in self.responses.items() if key.startswith(prefix))


class Stop(BaseModel, frozen=True):
    """A person restarts the crawl after `beats` progress beats: the run says stop, the crawl gets SIGTERM and saves
    its queue, and a second attempt carries on where it got to."""

    kind: Literal["stop"] = "stop"
    beats: int

    def watch(self, run_id: uuid.UUID, crawl: CrawlProcess) -> RecordingWatch:
        return StoppingWatch(run_id, 1, self.beats)


class Kill(BaseModel, frozen=True):
    """The worker dies after `beats` beats: the crawl is killed outright (SIGKILL) and the host does no cleanup. A
    second attempt must start over, keeping the files already downloaded."""

    kind: Literal["kill"] = "kill"
    beats: int

    def watch(self, run_id: uuid.UUID, crawl: CrawlProcess) -> RecordingWatch:
        return KillingWatch(run_id, 1, self.beats, crawl)


Interruption = Annotated[Stop | Kill, Field(discriminator="kind")]


class Observed(BaseModel, frozen=True):
    """What one run of a scenario saw, through the host's report and the portal's own ledger. `raised` is the
    exception the host's `Crawls.live` ended with, if it didn't return a report."""

    seconds: float
    raised: str | None = None
    crawl: Crawl | None = None
    stats: CrawlStats | None = None
    external_ids: tuple[str, ...] = ()
    files_saved: int = 0
    log: str = ""
    beats: int = 0
    served: Served
    reuploads: int = 0
    peak_rss_kb: int = 0

    @property
    def duplicates(self) -> int:
        return len(self.external_ids) - len(set(self.external_ids))

    @property
    def files_failed(self) -> int:
        return self.stats.files_failed if self.stats is not None else 0


class Expectation(BaseModel, frozen=True):
    """What a scenario must end with. `complete`: every reachable bid exactly once, every good file saved and every
    bad one counted as failed, nothing downloaded or uploaded twice. `raises` names the exception `Crawls.live` must
    end with instead of a report; `log` a line the crawl's stderr must hold; `reason` a phrase one of its error
    examples must hold."""

    complete: bool = True
    verdict: Verdict | None = Verdict.HEALTHY
    raises: str | None = None
    rate_limited: bool = False
    challenged: bool = False
    renewed: bool = False
    log: str | None = None
    reason: str | None = None

    def failures(self, seen: Observed, truth: Truth) -> tuple[str, ...]:
        """Each way `seen` falls short of this expectation, for the report."""
        if self.raises is not None:
            return () if seen.raised == self.raises else (f"raised {seen.raised}, expected {self.raises}",)
        if seen.crawl is None:
            return (f"raised {seen.raised}",)
        failed: list[str] = []
        if self.verdict is not None and seen.crawl.verdict != self.verdict:
            failed.append(f"verdict {seen.crawl.verdict}")
        if self.complete:
            if len(set(seen.external_ids)) != truth.reachable:
                failed.append(f"{len(set(seen.external_ids))}/{truth.reachable} listings")
            if seen.duplicates:
                failed.append(f"{seen.duplicates} duplicate ids")
            if seen.files_saved != truth.good_files:
                failed.append(f"{seen.files_saved}/{truth.good_files} files saved")
            if seen.files_failed != truth.bad_files:
                failed.append(f"{seen.files_failed}/{truth.bad_files} failed files counted")
            if seen.served.refetched:
                failed.append(f"{seen.served.refetched} files downloaded twice")
            if seen.reuploads:
                failed.append(f"{seen.reuploads} files uploaded twice")
        if self.rate_limited and not seen.crawl.rate_limited:
            failed.append("no rate limit counted")
        if self.challenged and not seen.crawl.challenged:
            failed.append("no challenge counted")
        if self.renewed and not seen.crawl.renewed:
            failed.append("no session renewed")
        if self.log is not None and self.log not in seen.log:
            failed.append(f"no {self.log!r} in stderr")
        if self.reason is not None and not any(self.reason in error for error in seen.crawl.errors):
            failed.append(f"no {self.reason!r} in errors")
        return tuple(failed)


class Scenario(BaseModel, frozen=True):
    """One named way the world behaves during a crawl, and what the crawl must make of it. `stall` is the site's
    `stall` in `buyer.toml` (minutes); `tuning` the Scrapy settings the spider runs with; `bucket_down` makes every
    upload fail, as an R2 outage would."""

    name: str
    about: str
    knobs: Knobs = Knobs()
    expect: Expectation = Expectation()
    interruption: Interruption | None = None
    stall: float = 5.0
    tuning: dict[str, int] = {}
    bucket_down: bool = False


class Outcome(BaseModel, frozen=True):
    """A scenario's run, judged."""

    scenario: Scenario
    truth: Truth
    seen: Observed
    failures: tuple[str, ...]

    HEADER: ClassVar[str] = (
        f"{'scenario':<16} {'secs':>6} {'req/s':>6} {'list/s':>6} {'listings':>10} {'saved':>11} {'failed':>7} "
        f"{'rl':>4} {'slow':>5} {'chal':>4} {'err':>3} {'beat':>4} {'rssMB':>5}  result"
    )

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def row(self) -> str:
        seen, crawl = self.seen, self.seen.crawl
        listings = len(set(seen.external_ids))
        verdict = crawl.verdict.value if crawl is not None else f"raised {seen.raised}"
        result = "PASS" if self.passed else "FAIL: " + "; ".join(self.failures)
        return (
            f"{self.scenario.name:<16} {seen.seconds:>6.1f} {seen.served.total / seen.seconds:>6.0f} "
            f"{listings / seen.seconds:>6.1f} {listings:>4}/{self.truth.listings:<5} "
            f"{seen.files_saved:>5}/{self.truth.good_files:<5} {seen.files_failed:>7} "
            f"{crawl.rate_limited if crawl else 0:>4} {crawl.slowed_to if crawl else 0:>5.1f} "
            f"{crawl.challenged if crawl else 0:>4} {len(crawl.errors) if crawl else 0:>3} {seen.beats:>4} "
            f"{seen.peak_rss_kb // KB_PER_MB:>5}  {verdict}, {result}"
        )
