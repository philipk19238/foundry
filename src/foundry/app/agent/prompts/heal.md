The scraper for {url} (site `{site}`, version {version}) stopped working.

What the last crawl saw: {seen}.
Errors:
{errors}

The site's spider and the fixtures it was checked against are in your clone, at the paths `buyer.toml` names for
`{site}`. Open the portal, see what changed (a new API path, renamed fields, a new sign-in step), capture fresh
responses into the fixtures, and fix the spider until `check_scraper` reports `healthy` and the live crawl
(`foundry.core live {site}`, as your instructions show) prints `PASS`. Then `submit` the site. If
the spider is a shared platform spider, keep every other buyer on that platform passing too.
