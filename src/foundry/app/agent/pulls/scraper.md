{% set crawl = staging.crawl %}
**Portal:** {{ portal }}
**Site:** `{{ submission.site }}` · **Run:** [{{ run_id }}]({{ run_url }}) · **Source:** [scraper versions]({{ source_url }})

## What it does

{{ submission.summary }}

## How it gets the data

**Reads:** {{ {"api": "the portal's API", "html": "the HTML the portal's server sends", "browser": "a real browser"}[submission.method] }}
**Access:** {{ submission.access }}

{% for step in submission.process %}
{{ loop.index }}. {{ step }}
{% endfor %}

## Testing

- **Try it live:** open the [source]({{ source_url }}) and press **▶ Try it**, or call `start_run(source_id="{{ source_id }}", kind="trial")` over MCP. It crawls the live portal with this branch's scraper and lists every listing without storing any.
- **Replay locally:** `git fetch origin {{ branch }} && git checkout {{ branch }} && uv run python -m foundry.core check {{ submission.site }}`
- **Ask the agent:** write in the [run's chat]({{ run_url }}); it picks up in the same conversation and updates this pull request.

| Check | Result |
|---|---|
{% for key, report in replays.items() %}
| Replay of `{{ key }}`'s fixtures | {{ report.crawl.verdict }}: {{ report.crawl.valid }} listings, {{ report.crawl.invalid }} invalid |
{% endfor %}
| Staging crawl of the live portal from this branch | {{ crawl.verdict }}: {{ crawl.valid }} listings, {{ crawl.invalid }} invalid{{ ", crashed" if crawl.crashed }} |
{% for error in crawl.errors %}
| Staging error | {{ error | cell }} |
{% endfor %}
{% for check in submission.checks %}
| Checked against the portal | {{ check | cell }} |
{% endfor %}

## Sample output

{% if samples %}
{{ samples | length }} of {{ crawl.valid }} listings from the staging crawl:

| Title | Portal id | Buyer | Due | Files |
|---|---|---|---|---|
{% for listing in samples %}
| [{{ listing.title | cell }}]({{ listing.url }}) | {{ (listing.external_id or "(none: keyed by title)") | cell }} | {{ (listing.buyer or "") | cell }} | {{ listing.due_at or "" }} | {{ listing.files | length }} |
{% endfor %}

<details><summary>The first listing, as the scraper yields it</summary>

```json
{{ first }}
```

</details>
{% else %}
The staging crawl yielded no listings.
{% endif %}

## Known gaps

{% for gap in submission.gaps %}
- {{ gap }}
{% else %}
None reported.
{% endfor %}

## Review checklist

- [ ] The sample listings match what the portal shows (open two of the links).
- [ ] The count matches the portal's open opportunities, or the gaps above explain the difference.
- [ ] `external_id` is the portal's own id, which stays the same when the portal edits a listing.
- [ ] Each file link downloads the file itself.
- [ ] The fixtures hold nothing private beyond what the portal shows any signed-in user.

Merging registers this scraper; foundry picks the merge up and refreshes the source from `main`.
