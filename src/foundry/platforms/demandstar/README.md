# DemandStar / Euna OpenBids

Tenant is the agency UUID at the end of its public procurement-opportunities URL.
GET contents/agency/search?id=<UUID> returns the agency's opportunities (including
closed records); only statusType AC is scraped. The full result is returned at once;
the page's View more control reveals more rows without another API request.

POST contents/agency/summary and documents take {"bidId": <numeric ID>}.
The spider also includes bidId in the query string, verified against the live API,
to distinguish fixtures because Foundry replay keys on method and URL.
Summary dueDate is UTC without an offset, verified against the page's Pacific display.

No account is needed for these public endpoints. Documents include names, IDs,
types and sizes, but blank download paths. Clicking Download all documents opens
a login dialog. The operator explicitly requested account-free scraping, so files
are empty and document metadata is retained. No download URL is guessed.

The API includes visitLimitExceeded; the spider fails explicitly if that limit
is reached. Fixtures contain the listing and summaries/documents for two active bids.
