---
name: learnings
description: Read what earlier agents learned about portals, platforms and the kit, and save what you learn for the agents after you with the remember tool. Use at the start of every run (read the ones that apply), and before ending your turn (save what was non-obvious).
---

# Learnings

Every agent shares one set of learnings. Your instructions start with their index; each one is a file in
`$FOUNDRY_LEARNINGS/<name>.md`. They're how this run benefits from every run before it.

## At the start

Read every learning whose `about` matches your portal's platform or buyer, and the `general` ones that touch what
you're about to do:

```bash
cat "$FOUNDRY_LEARNINGS"/<name>.md
grep -il planetbids "$FOUNDRY_LEARNINGS"/*.md
```

A learning says what was true when it was written (its date is in the file). If the portal now behaves differently,
trust the portal, and update the learning.

## Before you end

Ask: what did this run cost me that the next agent shouldn't have to pay for? Save each answer with `remember`:

- **One fact per learning**: "PlanetBids' API rate-limits above about 2 requests a second with 429s and AWS WAF
  challenges; DOWNLOAD_DELAY 1 with concurrency 1 passes."
- **Why it holds and how to apply it** in the body, with the evidence (status codes, the endpoint, what you
  compared).
- **Specific `about`**: `platform:planetbids` beats `general` when it only applies there.
- **Update instead of duplicating**: if a learning with that subject exists, save under its name with the new
  content. Fix a learning that turned out wrong the same way.

Worth saving: a platform's endpoints, headers, tenant format, limits, time zone; a trap the skills don't cover; a
fix for an environment problem; where public copies of files live. Not worth saving: anything the skills or the
code already say, what you did this run, or anything secret (never a password, token or cookie).
