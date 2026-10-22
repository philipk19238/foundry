---
name: changing-foundry
description: Fix foundry's own code when it is what blocks you - a missing tool, a bug in foundry.core, wrong instructions or skills - and get the change merged and the run restarted on it. Use only when the blocker is foundry, not the portal or your spider.
---

# Changing foundry itself

1. **Fix it in your clone**, small and about the blocker. Skills live in `src/foundry/app/agent/skills/`,
   instructions in `src/foundry/app/agent/prompts/`, the scraper kit in `src/foundry/core/`.
2. **Test it.** Every crawl runs on the `foundry.core` foundry is deployed with, yours included; to try your change,
   run with `PYTHONPATH=src`, e.g. `PYTHONPATH=src "$FOUNDRY_PYTHON" -m foundry.core live <site> --browser
   "$FOUNDRY_BROWSER"`. Replay every site after a kit change: `PYTHONPATH=src "$FOUNDRY_PYTHON" -m foundry.core
   check --all`.
3. **`propose_change`** with what changes, the blocker, and how you tested it; its fields fill the pull request.
4. **`request_help`** asking a person to review and merge it, with the link.
5. Once it's merged, **`restart`** and end your turn. The run comes back on the new code, in this same
   conversation; rebase your branch onto `origin/main` to have the change in your checkout too.

Keep scraper work and foundry changes apart: a change pull request holds only the fix.
