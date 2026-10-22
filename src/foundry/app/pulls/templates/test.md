**Test scrape of v{{ scraper.version }}** against the live portal: {{ crawl.verdict }}, {{ crawl.valid }} listings{% if crawl.invalid %}, {{ crawl.invalid }} invalid{% endif %}{% if crawl.crashed %}, crashed{% endif %}. [The job]({{ run_url }}) · [Test tab]({{ test_url }})
{% if crawl.refused %}

The portal turned the scraper away: {{ crawl.refused }}.
{% endif %}
{% if crawl.errors %}

| Error |
|---|
{% for error in crawl.errors[:10] %}
| {{ error | cell }} |
{% endfor %}
{% endif %}

{% if samples %}
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
The test scrape found no listings.
{% endif %}
