"""Runs scenarios through the real host code and prints how each went:

    uv run python -m stress.run                 every scenario
    uv run python -m stress.run baseline hang   the ones named
    uv run python -m stress.run --list          their names and what each does

Each scenario starts the fake portal, builds a repo tree holding its site, and calls `Crawls.live` with a local
Chromium, an in-memory bucket and a fake `CrawlWatch`, exactly as a worker does; an interrupted one then calls it
again as the run's next attempt."""

import argparse
import asyncio
import sys
import tempfile
import time
import uuid
from pathlib import Path

from foundry.app.browser.browser import Browsers
from foundry.app.scrapers.models.crawl import CrawlReport
from foundry.app.scrapers.scrapers import CrawlFolders, Crawls
from foundry.core.models import Access, CrawlJob, CrawlStats
from stress.browsers import LocalBrowserApi
from stress.buckets import DownBucket, MemoryBucket
from stress.models import Observed, Outcome, Scenario
from stress.portal import Portal
from stress.process import CrawlProcess, PeakMemory
from stress.scenarios import SCENARIOS
from stress.tree import Tree
from stress.watches import RecordingWatch

ERRORS_SHOWN = 3


class ObservedCrawls(Crawls):
    """`Crawls` that also keeps the last finished crawl's own stats and stderr, which the host deletes with the run's
    folder: its report carries neither the failed downloads nor the log."""

    def __init__(self, browsers: Browsers, bucket: MemoryBucket, state: CrawlFolders) -> None:
        super().__init__(browsers, bucket, state)
        self.stats: CrawlStats | None = None
        self.log = ""

    def _report(self, job: CrawlJob, returncode: int | None, log: Path, previous_valid: int) -> CrawlReport:
        if job.stats.exists():
            self.stats = CrawlStats.model_validate_json(job.stats.read_text())
        self.log = log.read_text(errors="replace") if log.exists() else ""
        return super()._report(job, returncode, log, previous_valid)


class ScenarioRun:
    """One scenario, run in its own scratch folder against its own portal."""

    def __init__(self, scenario: Scenario, browsers: LocalBrowserApi, scratch: Path) -> None:
        self._scenario = scenario
        self._browsers = browsers
        self._scratch = scratch

    async def outcome(self) -> Outcome:
        scenario = self._scenario
        bucket = DownBucket() if scenario.bucket_down else MemoryBucket()
        state = CrawlFolders(self._scratch / "state")
        crawls = ObservedCrawls(Browsers(self._browsers), bucket, state)
        run_id = uuid.uuid4()
        attempts = [CrawlProcess(state.folder(run_id) / str(attempt) / "job.json") for attempt in (1, 2)]
        watches = (
            [scenario.interruption.watch(run_id, attempts[0]), RecordingWatch(run_id, 2)]
            if scenario.interruption is not None
            else [RecordingWatch(run_id, 1)]
        )
        report: CrawlReport | None = None
        raised: str | None = None
        async with Portal(scenario.knobs) as portal, PeakMemory(attempts) as memory:
            tree = Tree(self._scratch / "tree")
            root = tree.build(portal.tenant, scenario.stall, scenario.tuning)
            began = time.monotonic()
            for watch in watches:
                try:
                    report, raised = await crawls.live(root, uuid.uuid4(), tree.key, Access(), 0, watch, {}), None
                except Exception as error:  # noqa: BLE001 - how the host's call ended is what the scenario checks
                    report, raised = None, type(error).__name__
            seconds = time.monotonic() - began
            served = portal.served
        seen = Observed(
            seconds=seconds,
            raised=raised,
            crawl=report.crawl if report is not None else None,
            stats=crawls.stats if report is not None else None,
            external_ids=tuple(listing.external_id or "" for listing in report.listings) if report else (),
            files_saved=len(report.files) if report is not None else 0,
            log=crawls.log,
            beats=sum(len(watch.beats) for watch in watches),
            served=served,
            reuploads=bucket.reuploads,
            peak_rss_kb=memory.peak_kb,
        )
        truth = scenario.knobs.truth
        return Outcome(scenario=scenario, truth=truth, seen=seen, failures=scenario.expect.failures(seen, truth))


async def run(scenarios: list[Scenario]) -> bool:
    print(Outcome.HEADER, flush=True)
    outcomes: list[Outcome] = []
    async with LocalBrowserApi() as browsers:
        for scenario in scenarios:
            with tempfile.TemporaryDirectory(prefix=f"stress-{scenario.name}-") as scratch:
                outcome = await ScenarioRun(scenario, browsers, Path(scratch)).outcome()
            outcomes.append(outcome)
            print(outcome.row, flush=True)
            print(f"    served: {outcome.seen.served.responses}", flush=True)
            if outcome.seen.crawl is not None:
                for error in outcome.seen.crawl.errors[:ERRORS_SHOWN]:
                    print(f"    error: {error[:240]!r}", flush=True)
    return all(outcome.passed for outcome in outcomes)


def main() -> None:
    parser = argparse.ArgumentParser(prog="stress.run", description="Foundry's live crawl against a fake portal.")
    parser.add_argument("names", nargs="*", help="scenarios to run (all when none)")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    options = parser.parse_args()
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    if options.list:
        for scenario in SCENARIOS:
            print(f"{scenario.name:<16} {scenario.about}")
        return
    unknown = [name for name in options.names if name not in by_name]
    if unknown:
        parser.error(f"no scenario named {', '.join(unknown)}; see --list")
    chosen = [by_name[name] for name in options.names] if options.names else list(SCENARIOS)
    sys.exit(0 if asyncio.run(run(chosen)) else 1)


if __name__ == "__main__":
    main()
