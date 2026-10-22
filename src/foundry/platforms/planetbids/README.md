# PlanetBids

Tenants are numeric agency IDs in /portal/<id>/bo/bo-search.
The public papi API requires Origin/Referer and company-id application headers;
no account or authorization token is needed for these endpoints.
The agency endpoint supplies the buyer name and local time zone.

The spider paginates all published opportunities and preserves stageStr.
Open opportunities use stage_id=3 (Bidding); Menifee had none at onboarding.
Historical notices provide replay examples and remain discoverable.

Details and bid-downloadable-files are public. Document metadata supplies the
exact serverFullPath and serverFilename. Combining these produces a direct file
URL verified anonymously with HTTP 200, although the UI marks the sample
files as requiring login. No registration was created.
