# Paso Robles: DemandStar with public city documents

The DemandStar agency UUID is 68ab6996-941d-43f0-bf43-a252f26c653d.
GET contents/agency/search?id=<UUID> returns the complete agency listing.
Only statusType AC is scraped. View more reveals rows locally.
POST contents/agency/summary and documents take {"bidId": <numeric ID>}.
A verified bidId query parameter distinguishes the POST fixtures by URL.
Summary dueDate is UTC without an offset, verified against the Pacific display.

The city publishes some documents without login at https://www.prcity.com/Bids.aspx.
The scraper discovers city detail pages by exact normalized opportunity title,
then verifies the bid number before collecting .relatedDocuments PDF links.
These URLs are discovered each crawl, not hardcoded to the two sample bids.
Failed city requests preserve the DemandStar listing.

Verified anonymous downloads:
- DocumentCenter/View/41690: 53-page emergency response RFP (663,417 bytes).
- DocumentCenter/View/41598: Riverside notice inviting bids (100,385 bytes).

DemandStar's actual download endpoint, discovered in its public application bundle,
is GET /documents/content/v1/document/download?docId=<document ID>&id=<bid ID>&type=Bid.
It returned HTTP 401 anonymously. No account was created.
The city does not link the RFP addendum, Riverside plans or Riverside full contract
documents, so this supplement does not claim complete attachment coverage.
DemandStar document metadata is retained to expose those gaps.

This is buyer-specific code because document discovery uses the city's website.
Fixtures include the original DemandStar listing, two summaries and document lists,
plus the city listing and corresponding two city detail pages.
