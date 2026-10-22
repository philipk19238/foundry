---- MODULE Scraper ----
\* One source's whole lifecycle: its lease, the runs on it (agent jobs, crawls, test crawls, the merge check, an imported
\* pull request), deferral and waking, the reaper, a person's chat reaching the agent, versions and their pull requests,
\* and the source's status. Every action names the code it stands for. Help requests, restarts and stalled heartbeats
\* are in formal/runs/Runs.tla and are left out here; a worker that dies stops renewing, so its lease lapses.
\*
\* One slot per run kind: a request for a kind whose run is still active is dropped (the real second run would be
\* skipped), except a heal after a broken crawl, which waits (wantHeal) as the real queued heal run would.
EXTENDS Naturals, FiniteSets

CONSTANTS MaxV,           \* versions (and pull requests) a source can get
          Msgs,           \* messages people send to the agent
          Deaths,         \* workers that die while holding the source
          Asks,           \* agent jobs a person asks for after the first onboard
          MaxHeals,       \* scrapers/models.py MAX_HEALS
          Crashes,        \* an agent job can fail with an exception (exceptions.AgentFailed, a crash)
          Trials,         \* people start test crawls
          Imports,        \* a person's own pull request is imported (app.py Imports)
          PersonRuns,     \* people start crawls and merge checks by hand (routes/scrapers.py start_run, mcp start_run)
          ScheduleWakes,  \* app.py Jobs.review_pending wakes jobs stranded on a source nothing holds
          FinallyWakesAll,\* SHIPPED: Jobs.carry_out's finally wakes the source even when the run never got the lease
          RereadOnSay,    \* FIX: RunChat.say re-reads the run after writing and reopens it if it has ended meanwhile
          ReviewAll,      \* FIX: the merge check settles any decided version, earliest merged first, not just the newest
          SettleOnCrash,  \* FIX: an agent job that fails settles the source's status as a rejection would
          ApproveLeased   \* FIX: Reviews.approve checks and merges under the source's lease (Leases.hold)

None == "none"
A == "a"   \* the agent job (onboard, heal; relogin is left out)
F == "f"   \* the crawl (refresh)
V == "v"   \* the merge check (review)
T == "t"   \* the test crawl (trial), which holds no lease
I == "i"   \* Imports._propose's run
P == "p"   \* Reviews.approve holding the lease from its agent check to its merge (ApproveLeased)
Runs == {A, F, V, T, I}
Leased == {A, F, V, I}                       \* db/models/runs.py RunKind.leased
Active == {"queued", "deferred", "running"}  \* runs/models.py ACTIVE (waiting, restarting left out)
Ended == {"succeeded", "failed", "skipped"}  \* runs/store.py _ENDED
Versions == 1..MaxV
Pulls == 1..MaxV
Gone == {"idle", "dead"}                     \* no live worker carries the run

VARIABLES holder, lapsed,                \* sources.lease_run_id; lease_expires_at <= now
          status, pc, again,             \* runs.status; each run's worker; A was dispatched again while its worker ran
          kind, ran, reopened, wantHeal, \* A's kind; A got the lease since it was last queued; A is a reopened run
          unread, sent, say, autoReopens,\* people's lines past the agent's read cursor (Workspace.read_up_to)
          vstate, vpull, prs, mseq, current, runPull,
          apc, badMerge,
          st, heals, crawled, follow,
          deaths, asks, imported, spin

lease == <<holder, lapsed>>
runs == <<status, pc, again>>
agent == <<kind, ran, reopened, wantHeal>>
chat == <<unread, sent, say, autoReopens>>
code == <<vstate, vpull, prs, mseq, current, runPull>>
review == <<apc, badMerge>>
source == <<st, heals, crawled, follow>>
flags == <<deaths, asks, imported, spin>>
vars == <<lease, runs, agent, chat, code, review, source, flags>>

TypeOK ==
    /\ holder \in Leased \cup {P, None} /\ lapsed \in BOOLEAN
    /\ status \in [Runs -> {"none"} \cup Active \cup Ended]
    /\ pc \in [Runs -> {"idle", "queued", "claim", "busy", "check", "wakeBusy", "work", "turn", "release", "wake",
                       "owed", "trial", "dead"}]
    /\ again \in BOOLEAN
    /\ kind \in {"onboard", "heal"} /\ ran \in BOOLEAN /\ reopened \in BOOLEAN /\ wantHeal \in BOOLEAN
    /\ unread \in BOOLEAN /\ sent \in 0..Msgs /\ say \in {"idle", "active", "ended"} /\ autoReopens \in 0..Msgs
    /\ vstate \in [Versions -> {"none", "proposed", "merged", "closed"}]
    /\ vpull \in [Versions -> 0..MaxV] /\ prs \in [Pulls -> {"none", "open", "merged", "closed"}]
    /\ mseq \in [Pulls -> 0..MaxV] /\ current \in 0..MaxV /\ runPull \in 0..MaxV
    /\ apc \in {"idle", "checked"} /\ badMerge \in BOOLEAN
    /\ st \in {"new", "onboarding", "ready", "healing", "in_review", "needs_human"}
    /\ heals \in 0..MaxHeals /\ crawled \in BOOLEAN /\ follow \in {"none", "heal", "refresh"}
    /\ deaths \in 0..Deaths /\ asks \in 0..Asks /\ imported \in BOOLEAN /\ spin \in BOOLEAN

\* A source added through the API or MCP starts with an onboard run (routes/api.py, routes/mcp.py add_source).
Init ==
    /\ holder = None /\ lapsed = FALSE
    /\ status = [r \in Runs |-> IF r = A THEN "queued" ELSE "none"]
    /\ pc = [r \in Runs |-> IF r = A THEN "queued" ELSE "idle"]
    /\ again = FALSE
    /\ kind = "onboard" /\ ran = FALSE /\ reopened = FALSE /\ wantHeal = FALSE
    /\ unread = FALSE /\ sent = 0 /\ say = "idle" /\ autoReopens = 0
    /\ vstate = [i \in Versions |-> "none"] /\ vpull = [i \in Versions |-> 0]
    /\ prs = [p \in Pulls |-> "none"] /\ mseq = [p \in Pulls |-> 0] /\ current = 0 /\ runPull = 0
    /\ apc = "idle" /\ badMerge = FALSE
    /\ st = "new" /\ heals = 0 /\ crawled = FALSE /\ follow = "none"
    /\ deaths = 0 /\ asks = 0 /\ imported = FALSE /\ spin = FALSE

\* runs/store.py _FREE and _STRANDED: nothing holds a live lease
Free == holder = None \/ lapsed
Live == current > 0
NV == Cardinality({i \in Versions : vstate[i] /= "none"})
NP == Cardinality({p \in Pulls : prs[p] /= "none"})
Merges == Cardinality({p \in Pulls : prs[p] = "merged"})
Proposed == {i \in Versions : vstate[i] = "proposed"}
\* scrapers/store.py _PROPOSED: the newest version still waiting in its pull request
Newest == IF Proposed = {} THEN 0 ELSE CHOOSE i \in Proposed : \A j \in Proposed : j <= i
Decided == {i \in Proposed : prs[vpull[i]] /= "open"}
Rank(i) == IF prs[vpull[i]] = "merged" THEN mseq[vpull[i]] ELSE 0
\* app.py Jobs._review: the version the merge check settles
ToSettle == IF ReviewAll
            THEN IF Decided = {} THEN 0 ELSE CHOOSE i \in Decided : \A j \in Decided : Rank(i) <= Rank(j)
            ELSE Newest
\* app.py Jobs.review_pending: `(await self._pulls.get(proposal.pull)).state == "closed"`
ReviewDue == IF ReviewAll THEN Decided /= {} ELSE Newest > 0 /\ prs[vpull[Newest]] /= "open"

\* scrapers/models.py Standing.working: the status while the agent works (`None` leaves it as it is)
Working(k) == IF k = "heal" THEN "healing" ELSE IF Live THEN st ELSE "onboarding"
\* scrapers/models.py Standing.settled
Settled(k, rejected, live, proposed) ==
    IF rejected /\ k = "heal" THEN "needs_human"
    ELSE IF live THEN "ready"
    ELSE IF proposed THEN "in_review"
    ELSE "needs_human"

SlotFree(s) == pc[s] = "idle" /\ status[s] \notin Active
\* Timescales: a dispatched run reaches the lease (or defers) and a released one finishes its finally within seconds,
\* while the schedule ticks every few minutes or hours and people start runs by hand. So the schedule and people's runs
\* start only while no worker is between dispatch and lease or after release; long work (an agent turn) is not waited on.
Quiet == \A r \in Runs : pc[r] \notin {"queued", "claim", "busy", "check", "wakeBusy", "wake", "owed"}
\* One agent run at a time in the model, and a person's line belongs to the run it was written to.
NewAgentOK == SlotFree(A) /\ say = "idle" /\ (~unread \/ status[A] = "failed")

\* runs/store.py _WAKE, an atomic DEFERRED -> QUEUED, then runs.py Queue.wake hands A to the dispatcher. r is the
\* actor that wakes and goes on to `next`; if A's own worker is still running, the new dispatch is remembered in `again`.
WokeA == status[A] = "deferred"
Wake(r, next) ==
    /\ status' = [status EXCEPT ![A] = IF WokeA THEN "queued" ELSE @]
    /\ pc' = [pc EXCEPT ![r] = next, ![A] = IF r /= A /\ WokeA /\ pc[A] \in Gone THEN "queued" ELSE @]
    /\ again' = (again \/ (WokeA /\ (r = A \/ pc[A] \notin Gone)))
NoWake(r, next) == pc' = [pc EXCEPT ![r] = next] /\ UNCHANGED <<status, again>>

\* runs.py Queue.request for a crawl or a merge check: a new run, dispatched
Request(s) ==
    /\ status' = [status EXCEPT ![s] = "queued"] /\ pc' = [pc EXCEPT ![s] = "queued"]

\* runs.py Queue.request for an agent job (routes/scrapers.py start_run, mcp start_run, Jobs._carry's heal)
NewAgent(k) ==
    /\ status' = [status EXCEPT ![A] = "queued"] /\ pc' = [pc EXCEPT ![A] = "queued"] /\ again' = FALSE
    /\ kind' = k /\ ran' = FALSE /\ reopened' = FALSE /\ runPull' = 0 /\ unread' = FALSE

\* ---------------------------------------------------------------- a run's worker: app.py Jobs.carry_out

\* Jobs.carry_out -> runs.py Runs.start (_START): RUNNING
Start(r) ==
    /\ pc[r] = "queued"
    /\ status' = [status EXCEPT ![r] = "running"]
    /\ pc' = [pc EXCEPT ![r] = IF r = T THEN "trial" ELSE "claim"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, flags>>

\* runs.py Leases.claim (_CLAIM: lease_run_id IS NULL OR lease_expires_at <= now). An agent job then sets the status
\* it works under (Jobs._build: Standing.working -> Healths.set).
ClaimOK(r) ==
    /\ pc[r] = "claim" /\ Free
    /\ holder' = r /\ lapsed' = FALSE
    /\ pc' = [pc EXCEPT ![r] = "work"]
    /\ IF r = A THEN ran' = TRUE /\ st' = Working(kind) ELSE UNCHANGED <<ran, st>>
    /\ UNCHANGED <<status, again, kind, reopened, wantHeal, chat, code, review, heals, crawled, follow, flags>>

\* Leases.claim raises SourceBusy
ClaimBusy(r) ==
    /\ pc[r] = "claim" /\ ~Free
    /\ pc' = [pc EXCEPT ![r] = "busy"]
    /\ UNCHANGED <<lease, status, again, agent, chat, code, review, source, flags>>

\* Jobs._carry `except exceptions.SourceBusy`: an agent job is deferred (Runs.defer, _DEFER: RUNNING -> DEFERRED),
\* any other run is skipped. Imports._propose lets SourceBusy escape, leaving its run RUNNING for the reaper.
Busy(r) ==
    /\ pc[r] = "busy"
    /\ CASE r = A -> /\ status' = [status EXCEPT ![A] = "deferred"]
                     /\ ran' = FALSE
                     /\ pc' = [pc EXCEPT ![A] = "check"]
         [] r = I -> /\ pc' = [pc EXCEPT ![I] = "idle"]
                     /\ UNCHANGED <<status, ran>>
         [] OTHER -> /\ status' = [status EXCEPT ![r] = "skipped"]
                     /\ pc' = [pc EXCEPT ![r] = "wakeBusy"]
                     /\ UNCHANGED ran
    /\ UNCHANGED <<lease, again, kind, reopened, wantHeal, chat, code, review, source, flags>>

\* Jobs._carry: `if await self._leases.free(run.source_id): await self._queue.wake(run.source_id)`
Check ==
    /\ pc[A] = "check"
    /\ IF Free THEN Wake(A, "wakeBusy") ELSE NoWake(A, "wakeBusy")
    /\ UNCHANGED <<lease, agent, chat, code, review, source, flags>>

\* Jobs.carry_out finally: `if run.kind.leased: await self._queue.wake(run.source_id)`. "wake" follows a release,
\* "wakeBusy" a SourceBusy; shipped code wakes after both. spin: A woke itself while the source is held.
FinallyWake(r) ==
    /\ pc[r] \in {"wake", "wakeBusy"}
    /\ LET next == IF r = A THEN "owed" ELSE "idle"
       IN IF pc[r] = "wake" \/ FinallyWakesAll
          THEN Wake(r, next) /\ spin' = (spin \/ (r = A /\ WokeA /\ ~Free))
          ELSE NoWake(r, next) /\ UNCHANGED spin
    /\ UNCHANGED <<lease, agent, chat, code, review, source, deaths, asks, imported>>

\* Jobs.carry_out: `if run.has_agent and await self._replies_owed(run.id)` -> Queue.reopen (_REOPEN). _replies_owed:
\* the run SUCCEEDED and Agent.unread.
Owed ==
    /\ pc[A] = "owed"
    /\ IF status[A] = "succeeded" /\ unread
       THEN /\ status' = [status EXCEPT ![A] = "queued"]
            /\ ran' = FALSE /\ reopened' = TRUE /\ autoReopens' = autoReopens + 1
            /\ pc' = [pc EXCEPT ![A] = "queued"]
       ELSE /\ pc' = [pc EXCEPT ![A] = IF again THEN "queued" ELSE "idle"]
            /\ UNCHANGED <<status, ran, reopened, autoReopens>>
    /\ again' = FALSE
    /\ UNCHANGED <<lease, kind, wantHeal, unread, sent, say, code, review, source, flags>>

\* runs.py Leases.hold finally -> Leases.release (_RELEASE); then Jobs._carry queues the follow-up run. Imports._propose
\* releases and returns: it never wakes the source.
Release(r) ==
    /\ pc[r] = "release" /\ holder = r
    /\ holder' = None /\ lapsed' = FALSE
    /\ follow' = "none"
    /\ wantHeal' = (wantHeal \/ follow = "heal")
    /\ LET ask == follow = "refresh" /\ SlotFree(F)
       IN /\ status' = [status EXCEPT ![F] = IF ask THEN "queued" ELSE @]
          /\ pc' = [pc EXCEPT ![r] = IF r = I THEN "idle" ELSE "wake", ![F] = IF ask THEN "queued" ELSE @]
    /\ UNCHANGED <<again, kind, ran, reopened, chat, code, review, st, heals, crawled, flags>>

\* ---------------------------------------------------------------- the work under the lease

\* agent.py Agent.run fails before the turn starts (Workspaces.prepare, Browsers.opened): exceptions.Error -> FAILED.
\* Codex.work's Inbox has not read anything yet.
PreCrash ==
    /\ Crashes /\ pc[A] = "work"
    /\ status' = [status EXCEPT ![A] = "failed"]
    /\ st' = IF SettleOnCrash THEN Settled(kind, TRUE, Live, Proposed /= {}) ELSE st
    /\ pc' = [pc EXCEPT ![A] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, heals, crawled, follow, flags>>

\* agent.py Codex.work starts the turn with the Inbox beside it; Inbox.deliver's first poll hands over every line
\* people wrote after the read cursor before the model's first step ends.
TurnStart ==
    /\ pc[A] = "work"
    /\ unread' = FALSE
    /\ pc' = [pc EXCEPT ![A] = "turn"]
    /\ UNCHANGED <<lease, status, again, agent, sent, say, autoReopens, code, review, source, flags>>

\* agent.py Inbox.deliver: a line written during the turn is steered in and the cursor moves past it
AgentRead ==
    /\ pc[A] = "turn" /\ unread
    /\ unread' = FALSE
    /\ UNCHANGED <<lease, runs, agent, sent, say, autoReopens, code, review, source, flags>>

\* outcomes.ScraperPull: Proposals.propose -> GitHubPulls.open (updates the branch's open pull request, else opens
\* one) -> Versions.propose (PROPOSED); Jobs._build sets READY if a version is live, else IN_REVIEW.
Submit ==
    /\ pc[A] = "turn" /\ NV < MaxV
    /\ LET i == NV + 1
           p == IF runPull > 0 /\ prs[runPull] = "open" THEN runPull ELSE NP + 1
       IN /\ vstate' = [vstate EXCEPT ![i] = "proposed"]
          /\ vpull' = [vpull EXCEPT ![i] = p]
          /\ prs' = [prs EXCEPT ![p] = "open"]
          /\ runPull' = p
    /\ st' = IF Live THEN "ready" ELSE "in_review"
    /\ status' = [status EXCEPT ![A] = "succeeded"]
    /\ pc' = [pc EXCEPT ![A] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, mseq, current, review, heals, crawled, follow, flags>>

\* outcomes.Replied (a reopened run that answered) and outcomes.Rejection; Jobs._build -> Standing.settled
Settle(rejected) ==
    /\ pc[A] = "turn" /\ (rejected \/ reopened)
    /\ st' = Settled(kind, rejected, Live, Proposed /= {})
    /\ status' = [status EXCEPT ![A] = IF rejected THEN "failed" ELSE "succeeded"]
    /\ pc' = [pc EXCEPT ![A] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, heals, crawled, follow, flags>>

\* the turn fails (exceptions.AgentFailed) or crashes: Jobs._carry finishes the run FAILED, leaving the status
TurnCrash ==
    /\ Crashes /\ pc[A] = "turn"
    /\ status' = [status EXCEPT ![A] = "failed"]
    /\ st' = IF SettleOnCrash THEN Settled(kind, TRUE, Live, Proposed /= {}) ELSE st
    /\ pc' = [pc EXCEPT ![A] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, heals, crawled, follow, flags>>

AgentEnds == Submit \/ Settle(TRUE) \/ Settle(FALSE) \/ TurnCrash

\* app.py Jobs._refresh -> Refresher.refresh (Versions.current raises NoScraper with no live version) -> Healths.record
\* (Health.after); Jobs._refresh returns NEXT[status] (a heal after a broken crawl)
Crawl ==
    /\ pc[F] = "work"
    /\ IF ~Live
       THEN /\ status' = [status EXCEPT ![F] = "failed"]
            /\ UNCHANGED <<st, heals, crawled, follow>>
       ELSE /\ status' = [status EXCEPT ![F] = "succeeded"]
            /\ crawled' = TRUE
            /\ \E healthy \in BOOLEAN :
                  IF healthy THEN st' = "ready" /\ heals' = 0 /\ follow' = "none"
                  ELSE IF heals >= MaxHeals THEN st' = "needs_human" /\ follow' = "none" /\ UNCHANGED heals
                  ELSE st' = "healing" /\ heals' = heals + 1 /\ follow' = "heal"
    /\ pc' = [pc EXCEPT ![F] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, flags>>

\* app.py Jobs._review: no version waiting -> SKIPPED; merged -> Jobs._merged (Versions.merged: MERGED and current,
\* READY, then a refresh); closed -> Versions.closed and Standing.settled(REVIEW, rejected=True); open -> SKIPPED
Review ==
    /\ pc[V] = "work"
    /\ LET i == ToSettle
       IN IF i = 0 \/ prs[vpull[i]] = "open"
          THEN /\ status' = [status EXCEPT ![V] = "skipped"]
               /\ UNCHANGED <<vstate, current, st, follow>>
          ELSE IF prs[vpull[i]] = "merged"
          THEN /\ vstate' = [vstate EXCEPT ![i] = "merged"] /\ current' = i
               /\ st' = "ready" /\ follow' = "refresh"
               /\ status' = [status EXCEPT ![V] = "succeeded"]
          ELSE /\ vstate' = [vstate EXCEPT ![i] = "closed"]
               /\ st' = Settled("review", TRUE, Live, Proposed \ {i} /= {})
               /\ status' = [status EXCEPT ![V] = "succeeded"] /\ UNCHANGED <<current, follow>>
    /\ pc' = [pc EXCEPT ![V] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, vpull, prs, mseq, runPull, review, heals, crawled, flags>>

\* app.py Imports.register -> Imports._propose: a run added and started at once, which holds the lease to propose
ImportStart ==
    /\ Imports /\ ~imported /\ SlotFree(I) /\ NV < MaxV
    /\ imported' = TRUE
    /\ status' = [status EXCEPT ![I] = "running"] /\ pc' = [pc EXCEPT ![I] = "claim"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, deaths, asks, spin>>

\* Imports._propose: Versions.propose on the person's pull request, then Standing.settled(REVIEW, False)
ImportPropose ==
    /\ pc[I] = "work"
    /\ LET i == NV + 1
           p == NP + 1
       IN /\ vstate' = [vstate EXCEPT ![i] = "proposed"] /\ vpull' = [vpull EXCEPT ![i] = p]
          /\ prs' = [prs EXCEPT ![p] = "open"]
    /\ st' = Settled("review", FALSE, Live, TRUE)
    /\ status' = [status EXCEPT ![I] = "succeeded"]
    /\ pc' = [pc EXCEPT ![I] = "release"]
    /\ UNCHANGED <<lease, again, agent, chat, mseq, current, runPull, review, heals, crawled, follow, flags>>

\* app.py Jobs._trial: a crawl from the newest version's own code, storing nothing, holding no lease
TrialEnd ==
    /\ pc[T] = "trial"
    /\ status' = [status EXCEPT ![T] = IF NV = 0 THEN "failed" ELSE "succeeded"]
    /\ pc' = [pc EXCEPT ![T] = "idle"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, flags>>

\* ---------------------------------------------------------------- failures and the reaper

\* The worker holding the source dies (container lost, OOM): it stops renewing and its finally never runs.
Die(r) ==
    /\ deaths < Deaths /\ holder = r /\ pc[r] \in {"work", "turn", "release"}
    /\ deaths' = deaths + 1
    /\ pc' = [pc EXCEPT ![r] = "dead"]
    /\ UNCHANGED <<lease, status, again, agent, chat, code, review, source, asks, imported, spin>>

\* runs.py LEASE passes with no renewal (Leases.renew is never called again)
Lapse ==
    /\ holder \in Runs /\ ~lapsed /\ pc[holder] \in Gone
    /\ lapsed' = TRUE
    /\ UNCHANGED <<holder, runs, agent, chat, code, review, source, flags>>

\* runs.py Runs.reap (_REAP): QUEUED, RUNNING or WAITING runs whose worker is gone and that hold no live lease; a trial
\* only after TRIAL_LIFETIME, which "its worker is gone" stands for
Reap(r) ==
    /\ status[r] \in {"queued", "running"} /\ pc[r] \in Gone
    /\ ~(holder = r /\ ~lapsed)
    /\ status' = [status EXCEPT ![r] = "failed"]
    /\ pc' = [pc EXCEPT ![r] = "idle"]
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, flags>>

\* ---------------------------------------------------------------- the schedule (worker/functions.py)

\* Jobs.refresh_ready: queues a refresh of a READY source. Each tick is a crawl owed again.
RefreshReady ==
    /\ Quiet
    /\ crawled' = FALSE
    /\ IF st = "ready" /\ SlotFree(F) THEN Request(F) ELSE UNCHANGED <<status, pc>>
    /\ UNCHANGED <<lease, again, agent, chat, code, review, st, heals, follow, flags>>

\* Jobs.review_pending, first loop: Leases.stranded -> Queue.wake
Stranded == ScheduleWakes /\ WokeA /\ Free
StrandedWake ==
    /\ Quiet /\ Stranded
    /\ Wake(V, pc[V])
    /\ UNCHANGED <<lease, agent, chat, code, review, source, flags>>

\* Jobs.review_pending, second loop: a merge check for a version whose pull request was merged or closed. A crawl lasts
\* minutes and this ticks every ten, so some tick finds no crawl under way.
ReviewPending ==
    /\ Quiet /\ pc[F] \notin {"work", "release"} /\ ReviewDue /\ SlotFree(V)
    /\ Request(V)
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, flags>>

\* the heal Jobs._carry queued after a broken crawl, carried out once the agent job before it ended
StartHeal ==
    /\ wantHeal /\ NewAgentOK
    /\ NewAgent("heal") /\ wantHeal' = FALSE
    /\ UNCHANGED <<lease, sent, say, autoReopens, vstate, vpull, prs, mseq, current, review, source, flags>>

\* ---------------------------------------------------------------- people and GitHub

\* routes/scrapers.py start_run, mcp start_run: an agent job
Ask(k) ==
    /\ Quiet /\ asks < Asks /\ NewAgentOK
    /\ NewAgent(k) /\ asks' = asks + 1
    /\ UNCHANGED <<lease, wantHeal, sent, say, autoReopens, vstate, vpull, prs, mseq, current, review, source,
                   deaths, imported, spin>>

\* routes/scrapers.py start_run, mcp start_run: a crawl or a merge check; a test crawl (from the same budget of asks)
AskRun(s) ==
    /\ \/ s \in {F, V} /\ PersonRuns
       \/ s = T /\ Trials
    /\ Quiet /\ asks < Asks /\ SlotFree(s) /\ Request(s) /\ asks' = asks + 1
    /\ UNCHANGED <<lease, again, agent, chat, code, review, source, deaths, imported, spin>>

\* help/help.py RunChat.say, first: `run = await self._history.run(run_id)`
SayRead ==
    /\ say = "idle" /\ sent < Msgs /\ status[A] /= "none"
    /\ say' = IF status[A] \in Active THEN "active" ELSE "ended"
    /\ UNCHANGED <<lease, runs, agent, unread, sent, autoReopens, code, review, source, flags>>

\* RunChat.say, then: Timeline.wrote, and `if not run.is_active: await self._queue.reopen(run_id)` on the run it read
\* (Queue.reopen -> _REOPEN: ended -> QUEUED, then dispatch)
SayWrite ==
    /\ say /= "idle"
    /\ unread' = TRUE /\ sent' = sent + 1 /\ say' = "idle"
    /\ LET reopen == (say = "ended" \/ (RereadOnSay /\ status[A] \in Ended)) /\ status[A] \in Ended
       IN /\ status' = [status EXCEPT ![A] = IF reopen THEN "queued" ELSE @]
          /\ pc' = [pc EXCEPT ![A] = IF reopen /\ @ \in Gone THEN "queued" ELSE @]
          /\ again' = (again \/ (reopen /\ pc[A] \notin Gone))
          /\ ran' = (ran /\ ~reopen) /\ reopened' = (reopened \/ reopen)
    /\ UNCHANGED <<lease, kind, wantHeal, autoReopens, code, review, source, flags>>

\* app.py Reviews.approve: `agent = await self._history.last_agent(source_id)`, refuse if active (AgentWorking);
\* refuse with nothing proposed (NothingToMerge). Approvals come from the same budget of asks.
\* ApproveLeased: the check runs under Leases.hold, so a busy source refuses (SourceBusy) and no agent can claim the
\* source and prepare its workspace until the merge is done.
ApproveCheck ==
    /\ apc = "idle" /\ asks < Asks /\ status[A] \notin Active /\ Newest > 0
    /\ apc' = "checked" /\ asks' = asks + 1
    /\ IF ApproveLeased THEN Free /\ holder' = P /\ lapsed' = FALSE ELSE UNCHANGED lease
    /\ UNCHANGED <<runs, agent, chat, code, badMerge, source, deaths, imported, spin>>

\* Reviews.approve, then: Versions.proposed, GitHubPulls.get, GitHubPulls.merge unless merged (GitHub refuses a closed
\* one: GitHubRefused), Queue.request(REVIEW)
ApproveMerge ==
    /\ apc = "checked" /\ apc' = "idle"
    /\ LET i == Newest
           p == vpull[i]
           merge == ~(i = 0 \/ prs[p] = "closed")
           check == merge /\ SlotFree(V)
           \* ApproveLeased: after Leases.hold, Queue.wake (as Imports._propose) for a job that deferred on approve
           wake == ApproveLeased /\ WokeA
       IN /\ IF merge /\ prs[p] = "open"
             THEN /\ prs' = [prs EXCEPT ![p] = "merged"] /\ mseq' = [mseq EXCEPT ![p] = Merges + 1]
                  /\ badMerge' = (badMerge \/ pc[A] \in {"work", "turn"})
             ELSE UNCHANGED <<prs, mseq, badMerge>>
          /\ status' = [status EXCEPT ![V] = IF check THEN "queued" ELSE @, ![A] = IF wake THEN "queued" ELSE @]
          /\ pc' = [pc EXCEPT ![V] = IF check THEN "queued" ELSE @,
                              ![A] = IF wake /\ pc[A] \in Gone THEN "queued" ELSE @]
          /\ again' = (again \/ (wake /\ pc[A] \notin Gone))
    /\ IF ApproveLeased THEN holder' = None /\ lapsed' = FALSE ELSE UNCHANGED lease
    /\ UNCHANGED <<agent, chat, vstate, vpull, current, runPull, source, flags>>

\* a pull request merged or closed on GitHub
GitHub(p) ==
    /\ prs[p] = "open"
    /\ \/ prs' = [prs EXCEPT ![p] = "merged"] /\ mseq' = [mseq EXCEPT ![p] = Merges + 1]
       \/ prs' = [prs EXCEPT ![p] = "closed"] /\ UNCHANGED mseq
    /\ UNCHANGED <<lease, runs, agent, chat, vstate, vpull, current, runPull, review, source, flags>>

\* ----------------------------------------------------------------

Worker(r) == Start(r) \/ Busy(r) \/ FinallyWake(r) \/ Release(r)
Steps(r) == Worker(r) \/ ClaimOK(r) \/ ClaimBusy(r)
AgentSteps == Check \/ Owed \/ TurnStart \/ AgentRead \/ PreCrash \/ AgentEnds
Work == AgentSteps \/ Crawl \/ Review \/ ImportPropose \/ TrialEnd
Schedule == RefreshReady \/ StrandedWake \/ ReviewPending \/ StartHeal \/ Lapse \/ \E r \in Runs : Reap(r)
People == \/ \E k \in {"onboard", "heal"} : Ask(k)
          \/ \E s \in {F, V, T} : AskRun(s)
          \/ SayRead \/ SayWrite \/ ApproveCheck \/ ApproveMerge \/ ImportStart
          \/ \E p \in Pulls : GitHub(p)

Next == \/ \E r \in Runs : Steps(r) \/ Die(r)
        \/ Work \/ Schedule \/ People
        \/ UNCHANGED vars

Spec == Init /\ [][Next]_vars

\* Workers, the schedule and the lease clock make progress; people and GitHub may never act. A run waiting to claim
\* is not forced to try while the source is held (ClaimBusy has no fairness), and a source that is free again and again
\* is eventually won by it (strong fairness on ClaimOK): no run loses every race forever. The schedule ticks only while
\* Quiet, so its fairness is strong too: a tick that is possible again and again eventually happens.
Fairness ==
    /\ \A r \in Runs : WF_vars(Worker(r)) /\ SF_vars(ClaimOK(r)) /\ WF_vars(Reap(r))
    /\ WF_vars(Check) /\ WF_vars(Owed) /\ WF_vars(TurnStart) /\ WF_vars(AgentEnds)
    /\ WF_vars(Crawl) /\ WF_vars(Review) /\ WF_vars(ImportPropose) /\ WF_vars(TrialEnd)
    /\ SF_vars(RefreshReady) /\ SF_vars(StrandedWake) /\ SF_vars(ReviewPending) /\ WF_vars(StartHeal)
    /\ WF_vars(Lapse) /\ (ApproveLeased => WF_vars(ApproveMerge))
LiveSpec == Spec /\ Fairness

\* ---------------------------------------------------------------- properties

\* At most one leased run works on the source at a time, and it is the lease holder.
OneLeased == \A r \in Leased : pc[r] \in {"work", "turn", "release"} => holder = r
\* A source with a live (merged) version is never shown as onboarding, new or in review: it stays ready while an agent
\* reworks it, healing while a heal works, or needs a person.
LiveStatus == Live => st \in {"ready", "healing", "needs_human"}
\* The agent is reopened on its own (Jobs._replies_owed) at most once per message people sent.
ReopensBounded == autoReopens <= sent
\* A deferred agent job never wakes itself while another run holds the source (no dispatch loop).
NoSpin == ~spin
\* The version the source runs was merged no earlier than any other registered version: no rollback.
NoRollback == Live => \A i \in Versions : vstate[i] = "merged" => mseq[vpull[i]] <= mseq[vpull[current]]
\* Approving never merges while an agent job has claimed the source and prepared its workspace (Workspaces.prepare
\* decided the branch has not landed, so its next push reopens a pull request that conflicts with the squash merge).
\* A job merely queued or deferred is harmless: it prepares after the merge and moves onto main (Clones.landed).
NoMergeWhileAgent == ~badMerge

\* Every message a person sends is eventually read by an agent turn, unless the run failed.
MessagesRead == unread ~> (~unread \/ status[A] = "failed")
\* A queued agent job eventually gets the source (or fails): it is never skipped and never lost.
AgentRuns == (status[A] = "queued") ~> (ran \/ status[A] = "failed")
\* A deferred agent job is never stranded: it eventually gets the source, even when its holder's worker dies.
DeferredRuns == (status[A] = "deferred") ~> ran
\* A pull request merged on GitHub or through approve is eventually registered.
MergedRegistered == \A i \in Versions : (vstate[i] = "proposed" /\ prs[vpull[i]] = "merged") ~> vstate[i] = "merged"
\* A source with a live version keeps being crawled, unless a person is needed (a rejected repair, too many heals).
KeepsCrawling == <>[](Live /\ st /= "needs_human") => []<>crawled

\* Vacuity guards: each must be violated, proving the situation is reachable.
NotDeferred == status[A] /= "deferred"
NotStranded == ~(status[A] = "deferred" /\ holder \in Runs /\ pc[holder] = "dead")
NotAutoReopened == autoReopens = 0
NotRegistered == current = 0
NotLiveRework == ~(Live /\ pc[A] = "turn" /\ kind = "onboard")
NotTwoProposed == Cardinality(Proposed) < 2
NotApproveWhileQueued == ~(apc = "checked" /\ status[A] = "queued")
====
