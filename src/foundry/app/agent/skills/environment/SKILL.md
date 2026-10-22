---
name: environment
description: Check the sandbox before starting - foundry's tools are present, the right Python works, the browser answers - and fix the known sandbox problems. Use at the start of every run, and whenever a command fails in a way that looks like the environment rather than the portal.
---

# Your environment

## Check it first

Before any portal work:

1. **Foundry's tools.** You should have `check_scraper`, `submit`, `request_help`, `account`, `credentials`,
   `save_credential`, `remember`, `propose_change` and `restart` from the `foundry` MCP server. If they're missing, say so once in
   a message, keep working on what doesn't need them, and never claim you submitted. (One run lost them all and only
   found out at the end.)
2. **Python.** `"$FOUNDRY_PYTHON" -c "import scrapy, playwright, foundry.core"`. Always run Python as
   `"$FOUNDRY_PYTHON"`; the shell's own `python` may lack Scrapy and Playwright.
3. **The browser.** Connect to it once:

   ```bash
   "$FOUNDRY_PYTHON" - <<'PY'
   import asyncio, os
   from playwright.async_api import async_playwright
   async def main():
       async with async_playwright() as p:
           browser = await p.chromium.connect_over_cdp(os.environ["FOUNDRY_BROWSER"])
           print("browser ok,", len(browser.contexts[0].pages), "pages")
   asyncio.run(main())
   PY
   ```

## Known problems

| Symptom | Cause | Fix |
|---|---|---|
| `Read-only file system: '/root/.local/...'` or `~/.cache` from a tool | home is read-only in the sandbox | foundry sets `XDG_STATE_HOME` and `XDG_CACHE_HOME`; if a tool ignores them, point it at `/tmp` |
| `No module named foundry` | `PYTHONPATH` lost | foundry sets it to the deployed kit; `PYTHONPATH=src` runs your clone's instead |
| `No module named scrapy` | the shell's `python` | use `"$FOUNDRY_PYTHON"` |
| `git push` fails | you can't push; foundry pushes your branch | commit, then `submit` or `propose_change` |
| your branch is behind `main` | things were merged since you started | `git fetch origin main && git rebase origin/main`; your `live` runs use the deployed kit anyway |
| a crawl reports a crash before any request | an environment error, not the parser | read the stderr it printed before changing the spider |

An environment failure is not a portal failure: say which it is in your messages, so a person fixes the right thing.
