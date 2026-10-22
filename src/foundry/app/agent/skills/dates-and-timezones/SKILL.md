---
name: dates-and-timezones
description: Get due dates and other timestamps right - find the portal's time zone, read offset-free API dates correctly, and check two deadlines against the time the page shows. Use for every scraper, since due dates are what people act on.
---

# Dates and time zones

`Listing.due_at` must be timezone-aware, and it must equal the deadline the portal shows a visitor. A wrong
offset moves a deadline by hours, and nobody notices until a bid is late.

## Find the zone

In this order:

1. **An offset in the data** (`2026-01-19T15:00:00-08:00`, or `Z`): use it as it is.
2. **The portal says it.** PlanetBids returns the agency's zone in `agencies/<tenant>` (`timeZoneName`). Others show
   "PT", "Eastern Time" or "(CST)" next to the deadline.
3. **The buyer's location.** A city or county portal with no zone anywhere is in the buyer's local zone:
   `ZoneInfo("America/Los_Angeles")` for California. Use `zoneinfo`, never a fixed offset, so daylight saving is
   right.

## Offset-free API dates are a guess until checked

An API date with no offset can be UTC or local. DemandStar's `dueDate` of `22:00` was UTC: the page showed 2:00 PM
Pacific. Before trusting either reading:

1. pick two opportunities;
2. note the deadline each page shows, with its zone;
3. convert the API's value both ways and keep the reading that matches both.

## In the spider

```python
from zoneinfo import ZoneInfo

local = ZoneInfo("America/Los_Angeles")
due = dt.datetime.fromisoformat(raw)  # "2026-01-19T15:00:00"
due_at = due.replace(tzinfo=local) if due.tzinfo is None else due  # local reading
# or, for a UTC API: due.replace(tzinfo=dt.UTC)
```

A date with no time ("due 01/19/2026") is due at the end of that day in the portal's zone, unless the page says
otherwise. Say which rule you used in `submit`'s `checks`: "due dates match the times the page shows (Pacific) for
bids 136642 and 136318".
