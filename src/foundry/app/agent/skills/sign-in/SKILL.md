---
name: sign-in
description: Decide whether a portal needs a sign-in, and when it does, how the scraper gets one - saved accounts, the browser session foundry keeps, tokens and API keys as saved credentials, CSRF tokens. Use when a request is refused, when data sits behind a login, or on a relogin job.
---

# Sign-ins, sessions and credentials

## Usually, don't

Most portals show their open opportunities, details and documents to anyone. Scrape what a visitor sees. A refused
request is not proof a sign-in is needed: firewalls and rate limits refuse too, and a missing header looks the same.
Check with the `live-crawl` skill first. If only the attachments need a login, keep each attachment's name and link
and say so in `gaps`.

## When the data really is behind a login

1. `account`: a login we already have for this portal.
2. None? Ask with `request_help` before signing up for anything, and never ask a person to make an account for you.
3. If you do sign up, call `save_account` with the username and password right away. Use a strong random password.

## What a crawl signs in with

- **The browser's session.** After every run and every scheduled crawl, foundry keeps everything the browser held:
  cookies on every domain, each origin's localStorage and sessionStorage, and its user agent. Every live crawl's
  browser starts with all of it, so a sign-in or a passed check carries over. A spider reads a stored token with
  `self.session.stored("<key>")` (None on replay, so parse without it). `needs` (the `find-the-api` skill) tells
  you which cookies and headers an endpoint actually needs and where they come from.
- **Saved credentials.** Any other secret (an API key, a token you generated, a second account) goes in
  `save_credential`; the spider reads it as `self.credentials["<name>"]`. `credentials` lists what is saved.
- **A CSRF token that changes on every page** is not a credential: read it from the page in the spider.

Never put a secret in the spider's code, its fixtures or the pull request.

## Relogin jobs

Confirm the session really expired: run the live crawl, and look at what the portal answered. Sign in again only
then, and check a protected page loads before you end.
