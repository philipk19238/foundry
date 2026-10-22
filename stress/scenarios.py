"""Every scenario the harness runs, by name. Sizes are kept to what shows the behaviour: the baseline and the sweep
crawl 1,000 bids with 3 files each; the slow faults (rate limits, challenges) fewer."""

from foundry.app.scrapers.models.crawl import Verdict
from stress.models import Expectation, Kill, Knobs, Scenario, Stop

SWEEP = ((16, 2), (16, 4), (16, 8), (32, 16), (64, 32))

SCENARIOS = (
    Scenario(name="baseline", about="1,000 bids, 3 files each, nothing goes wrong"),
    Scenario(
        name="rate-limit",
        about="a 40 req/s token bucket answering 429 with no Retry-After",
        knobs=Knobs(listings=300, rate=40, burst=5),
        expect=Expectation(rate_limited=True),
    ),
    Scenario(
        name="retry-after",
        about="the same limit, each 429 asking for 2 s",
        knobs=Knobs(listings=300, rate=40, burst=5, retry_after=2),
        expect=Expectation(rate_limited=True),
    ),
    Scenario(
        name="challenges",
        about="3% of bids always meet an AWS WAF challenge (202, empty body); the spider doesn't solve, and a local "
        "Chromium couldn't: they must be counted, the rest crawled, and the source sent to heal",
        knobs=Knobs(listings=120, challenged=0.03),
        expect=Expectation(verdict=Verdict.HEAL, challenged=True),
    ),
    Scenario(
        name="flaky",
        about="10% of API requests answer 500; Scrapy's retries must recover every one",
        knobs=Knobs(listings=500, flaky=0.1),
    ),
    Scenario(
        name="resets",
        about="5% of API requests have their connection dropped",
        knobs=Knobs(listings=500, resets=0.05),
    ),
    Scenario(
        name="expiring",
        about="signed file links expire 10 s after the detail issues them; downloads during the crawl beat that",
        knobs=Knobs(listings=300, link_seconds=10),
    ),
    Scenario(
        name="stale-links",
        about="5% of file links are already expired when issued (403 from the file host): counted as failed files, "
        "the crawl still healthy",
        knobs=Knobs(listings=200, stale=0.05),
    ),
    Scenario(
        name="viewers",
        about="10% of files are an HTML viewer page, not the file: counted as failed, with the reason",
        knobs=Knobs(listings=300, viewers=0.1),
        expect=Expectation(reason="got a web page, not the file"),
    ),
    Scenario(
        name="hang",
        about="1% of API requests never answer; the site's stall (0.5 min) must stop the crawl",
        knobs=Knobs(listings=500, hangs=0.01),
        stall=0.5,
        expect=Expectation(raises="CrawlStalled"),
    ),
    Scenario(
        name="session-drop",
        about="the portal's guest sessions last 80 API requests, then a request is sent to sign in; the crawl renews "
        "its session in the browser and carries on, with every bid",
        knobs=Knobs(listings=200, sessions_last=80),
        expect=Expectation(renewed=True),
    ),
    Scenario(
        name="restart",
        about="a person restarts the crawl after 2 beats (SIGTERM); the next attempt resumes",
        knobs=Knobs(latency=0.05),
        interruption=Stop(beats=2),
        expect=Expectation(log="Resuming crawl"),
    ),
    Scenario(
        name="kill",
        about="the worker dies after 2 beats (SIGKILL, no cleanup); the next attempt starts over, files kept",
        knobs=Knobs(latency=0.05),
        interruption=Kill(beats=2),
    ),
    Scenario(
        name="bucket-down",
        about="every upload fails: the crawl must fail loudly, its state kept for the next attempt",
        knobs=Knobs(listings=200),
        bucket_down=True,
        expect=Expectation(raises="BucketDown"),
    ),
    *(
        Scenario(
            name=f"sweep-{total}x{per_site}",
            about=f"1,000 bids at 50 ms a request; CONCURRENT_REQUESTS {total}, _PER_DOMAIN {per_site}",
            knobs=Knobs(latency=0.05),
            tuning={"CONCURRENT_REQUESTS": total, "CONCURRENT_REQUESTS_PER_DOMAIN": per_site},
        )
        for total, per_site in SWEEP
    ),
)
