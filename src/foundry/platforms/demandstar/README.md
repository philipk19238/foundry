# DemandStar public agency opportunities

The tenant is the UUID at the end of the buyer's `/app/agencies/<state>/<name>/procurement-opportunities/<UUID>/` URL.
The spider requests only that agency's public `/contents/agency/search?id=<UUID>` response, with no status filter.
It checks the returned count against `total`; the SAWS public page returned ten records. Its “View more” button
redirected anonymous visitors to login, so it is not anonymous pagination.

Details use POST `summary`, `commodityByType`, and `documents` under `/contents/agency/`, with a `bidId` body.
These endpoints accept `?bidId=<id>` as well, verified for both sample bids, making fixtures unambiguous.
Offset-free summary timestamps are UTC: SAWS bids 464668 and 463841 show 3 PM Central and return 20:00 UTC.
The two complete sample opportunities are in `fixtures.json`.

Documents retain their names and stable `/documents/content/v1/document/download?docId=...&id=...&type=Bid`
links. This download endpoint returns 401 anonymously. For externally distributed bids, `externalAgencyURL` in
metadata preserves the source linked by DemandStar. On the linked SAWS IonWave page, the original bid documents
also require login; no account is needed to scrape public details or document names. The separate award memo
on that page downloads publicly, but it is not a replacement for the restricted original specifications.
