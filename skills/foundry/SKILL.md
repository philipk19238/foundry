---
name: foundry
description: Drive Foundry through its MCP server - build scrapers for procurement portals with its agents, talk to an agent mid-job, test scrape and approve versions, import pull requests people wrote, and read the saved opportunities. Use when asked to onboard a portal, check on or steer a Foundry agent, review or merge a scraper, or query what Foundry has scraped. Covers following long jobs by waking yourself up on a schedule.
---

# Foundry over MCP

Foundry turns procurement portals into scrapers. Its MCP server does everything its UI does.

## Connect

Mint a token on the **API tokens** page, then add the server:

```bash
claude mcp add --transport http foundry https://<foundry host>/mcp/ --header "Authorization: Bearer $FOUNDRY_TOKEN"
```

Every tool acts as the token's person, with that person's role. Admins build, approve and start jobs; anyone can
read and talk to agents that are working.

## How a scraper moves

```
build_scraper ─▶ building ─▶ (needs_you ⇄ building) ─▶ review ─▶ approve ─▶ live
                    ▲                                      │                  │
                    └──────────── send_message (changes) ◀─┴──────────────────┘
```

| Stage | What is happening | What you do |
|---|---|---|
| `building` | An agent job (build, repair or sign in) is writing the scraper, or waiting its turn | Follow it with `wait_for_run`; steer it with `send_message` |
| `waiting` | The agent asked a question (`list_questions`) | Answer with `send_message`, or tell the human it needs them in the browser |
| `review` | Version N is in a pull request | `test_scrape`, read the listings, read the PR, then `approve` or `send_message` asking for changes |
| `live` | Merged; scraped every 6 hours | `list_opportunities`; `send_message` to the agent job for changes |
| `stopped` | Nothing is running and nothing is live | Read the job's summary; `send_message` to reopen it, or `start_job kind=onboard` |

Agent jobs have an agent you talk to. Scrapes (`refresh`, test scrapes) and merge checks run code only, so there
is nobody to message.

## Talking to an agent

The agent job is `get_scraper(...).progress.agent`. Talk to it the way you would to a colleague in a chat:

1. `send_message(run_id, text)`. While it works the message reaches it mid-task. While it waits on a question the
   message answers it. Once the job has ended the message reopens it in the same conversation, which is how you ask
   for changes to a live or in-review scraper.
2. `wait_for_run(run_id, after, seconds=50)` returns as soon as it writes back or its status changes. Pass back the
   `after` cursor from each result. `read_run(..., scope="everything")` shows every command and tool call when you
   need to see what it actually did.

### Listening instead of polling

A client on the 2026-07-28 MCP protocol can hear changes instead of calling `wait_for_run` in a loop. Pin it:
`Client(url_transport, mode="2026-07-28")` in the Python SDK. Left to negotiate, the SDK gives up on the newer protocol
after 10 s, and a cold server takes longer than that, so the client falls back to the old one, which can't listen. Each job and scraper is a
resource:

| Resource | What it is | Updated when |
|---|---|---|
| `foundry://runs/{run_id}/chat` | the job's chat, as `read_run` returns it | the job writes a line, or someone does |
| `foundry://runs/{run_id}` | the job, as `get_run` returns it | its status changes; a scrape beats (every 5 s, with progress) |
| `foundry://sources/{source_id}` | the scraper, as `get_scraper` returns it | any of its jobs changes status or beats |
| `foundry://questions` | the open questions, as `list_questions` returns them | any job's status changes |

```python
async with client.listen(resource_subscriptions=[f"foundry://runs/{run_id}/chat", f"foundry://runs/{run_id}"]) as sub:
    await client.call_tool("send_message", {"run_id": run_id, "text": "..."})
    async for event in sub:                       # event.uri says which one changed
        chat = await client.read_resource(event.uri)
```

An update carries no content: read the resource again. Read it once right after the listen opens, since nothing is
replayed. The host ends each stream after about 5 minutes (`SubscriptionLost` in the Python SDK): listen again and
read again. Updates arrive about a second after the change; a running scrape updates its run every 5 seconds with its
progress.

Be specific in messages: name the portal page, what is missing, and what done looks like. The agent cannot see this
conversation.

## Following long work: wait, then wake yourself up

Agent jobs take 10 minutes to over an hour, and reviews wait on people. Don't hold a turn open for that.

- **Inside a turn (up to about 5 minutes):** listen to the job (above), or loop `wait_for_run(run_id, after,
  seconds=50)` and pass the cursor back. Stop as soon as the status is `succeeded`, `failed` or `skipped`, or `waiting` is true.
- **Longer:** end the turn and wake yourself up on a schedule (`/loop`, or `ScheduleWakeup` in a dynamic loop). Pick
  the interval by what you are waiting for:

  | Waiting for | Wake every |
  |---|---|
  | An agent that is actively working | about 270 s (stays inside the 5-minute prompt cache) |
  | A job that is `deferred` (waiting its turn behind a scrape) | 270 s |
  | A person to answer, review or merge | 20 to 30 minutes |
  | A live scraper's next scheduled scrape | hours; usually don't poll at all |

  Never pick exactly 300 s. Each wake-up: `get_scraper`, then `read_run` from your saved cursor, then decide.
- **Stop polling** when the job has ended and there is nothing left for you to do, when it needs a human (tell
  them, with the scraper's page `url`), or after about 2 hours with no change (report that it seems stuck).
- On every wake-up, tell the human only what changed. Don't repeat unchanged status.

## Reviewing a version before you approve

1. `test_scrape(source_id)`, then `wait_for_run` on the returned job until it ends. `get_run(...).trial.listings` is
   everything it found; it saves nothing.
2. Check the count against what the portal page lists, and spot-check two listings (title, due date, files).
3. Read the pull request (its link is on the scraper). The template says how the scraper gets the data, what the
   agent checked and what it can't get yet.
4. `approve(source_id)` merges it and makes it live. It is refused while an agent job is working. When something is
   wrong, use `send_message` with what to change instead.

Approve only what you tested. If the test scrape found nothing because the portal asked to sign in, start
`start_job kind=relogin` first.

## Scrapers people wrote

A person can open a pull request that adds a `buyer.toml` and its spider without any agent. Open pull requests are
picked up every 10 minutes. `register_pull(number)` registers one at once, as a version in review on each portal it
touches, and then it is reviewed exactly like an agent's.

## Reading the data

`list_opportunities(source_id?, words?, page?)` returns the newest version of each saved opportunity, 50 a page;
`snapshots` counts how often the portal changed it. `get_file_link(notice_id, file_id)` gives a short-lived download
link for an attachment.

Everything a portal sent is kept too, as bronze. `get_bronze(run_id)` links a HAR of every response a scrape got
(attachments aside, what signs in hidden); an opportunity's `run_id` is the scrape that stored it. Use it to check a
field against what the portal actually returned. `get_recordings(run_id)` links what an agent job recorded while it
found the portal's API.

A refresh or test scrape that went quiet or failed carries on where it got to after `restart_scrape(run_id)`.

## Don't

- Don't paste secrets into messages. A credential belongs on the scraper's Access tab, where it is sealed.
- Don't approve while you are unsure, and don't approve a version you didn't test.
- Don't start `onboard` on a portal whose agent is still working; message the agent instead.
