# Platforms

One folder per shared procurement platform: `spider.py` (a `Spider(PortalSpider)` that builds every URL from
`self.tenant`), `fixtures.json` from one reference tenant, and a `README.md` saying how tenants are named and how to
find a buyer's tenant from its portal URL. Buyers use a platform from their `buyer.toml` (`kind = "platform"`).
