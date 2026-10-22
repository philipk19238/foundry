**Portal:** {{ portal }}
**Site:** `{{ submission.site }}` · **Run:** [{{ run_id }}]({{ run_url }}) · **Source:** [scraper versions]({{ source_url }})

## What it does

{{ submission.summary }}

## How it gets the data

{% if submission.method %}
**Reads:** {{ {"api": "the portal's API", "html": "the HTML the portal's server sends", "browser": "a real browser"}[submission.method] }}
{% endif %}
{% if submission.access %}
**Access:** {{ submission.access }}
{% endif %}

{% for step in submission.process %}
{{ loop.index }}. {{ step }}
{% endfor %}

## Testing

- **Test scrape:** one started when this pull request opened. Follow it on the [Test tab]({{ test_url }}); its results are commented here when it finishes. Run it again there, or with `test_scrape(source_id="{{ source_id }}")` over MCP. It crawls the live portal with this branch's scraper and stores nothing.
- **Replay locally:** `git fetch origin {{ branch }} && git checkout {{ branch }} && uv run python -m foundry.core check {{ submission.site }}`
- **Ask the agent:** write in the [run's chat]({{ run_url }}); it picks up in the same conversation and updates this pull request.

| Check | Result |
|---|---|
{% for key, report in replays.items() %}
| Replay of `{{ key }}`'s fixtures | {{ report.crawl.verdict }}: {{ report.crawl.valid }} listings, {{ report.crawl.invalid }} invalid |
{% endfor %}
{% for check in submission.checks %}
| Checked against the portal | {{ check | cell }} |
{% endfor %}

## Known gaps

{% for gap in submission.gaps %}
- {{ gap }}
{% else %}
None reported.
{% endfor %}

## Review checklist

- [ ] The test scrape's sample listings match what the portal shows (open two of the links).
- [ ] The count matches the portal's open opportunities, or the gaps above explain the difference.
- [ ] `external_id` is the portal's own id, which stays the same when the portal edits a listing.
- [ ] Each file link downloads the file itself.
- [ ] The fixtures hold nothing private beyond what the portal shows any signed-in user.

Merging registers this scraper; foundry picks the merge up and refreshes the source from `main`.
