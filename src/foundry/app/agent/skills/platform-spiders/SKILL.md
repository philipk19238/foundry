---
name: platform-spiders
description: Decide whether a portal gets a shared platform spider (PlanetBids, DemandStar, BidNet, OpenGov, Bonfire...) or a site of its own, and write a platform spider that works for every tenant without breaking the others. Use when starting a new buyer, and when changing any spider under src/foundry/platforms.
---

# Platform spiders

Many buyers run on the same hosted platform. One spider per platform serves them all, each buyer naming its
`tenant` in `buyer.toml`.

## Which kind

Look at the URL and the page: `vendors.planetbids.com/portal/<id>`, `demandstar.com/app/agencies/...`,
`bidnetdirect.com`, `procurement.opengov.com/portal/<name>`, `<name>.bonfirehub.com`, `ionwave.net`,
`periscopeholdings.com`, `bidsync.com`.

1. **`src/foundry/platforms/<platform>/` exists**: no code. Add a `[[portals]]` entry with `kind = "platform"`, the
   platform, this buyer's `tenant` and its `url`, and capture this tenant's fixtures (a listing and two details)
   into `src/foundry/buyers/.../<portal>.json`.
2. **A hosted platform other buyers use too, with no spider yet**: write it once in
   `src/foundry/platforms/<platform>/spider.py`, with `fixtures.json` from this buyer and a `README.md` saying how
   tenants are named and found. Then register the buyer as in 1.
3. **Anything built for this buyer alone** (its own website, a county's own system): `kind = "site"`, with
   `scraper.py` and `fixtures.json` in a folder for the portal inside the buyer's folder.

## Writing one for every tenant

- Build every URL and header from `self.tenant`; nothing about this buyer in the code. PlanetBids' tenant is the
  number in the portal URL and goes in a `company-id` header too.
- Read the buyer's name, time zone and settings from the platform's API when it has them (PlanetBids:
  `agencies/<tenant>`), not from constants.
- The README says how to find a tenant from a portal URL, so the next agent needs no research.

## Changing one

Every buyer on the platform runs this code. After any change, replay every site:

```bash
"$FOUNDRY_PYTHON" -m foundry.core check --all
```

Every one must still pass, and the `live-crawl` for this buyer must too.
