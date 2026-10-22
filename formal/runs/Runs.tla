---- MODULE Runs ----
\* Runs racing for one source's lease, the help handoff between a waiting agent, helpers and the expiry clock, fenced
\* writes, and a run restarting on newly deployed code. Every action names the code it stands for.
EXTENDS Naturals, FiniteSets

CONSTANTS Runs, Helpers, LeaseTTL, HelpTTL,
          RenewWhileWaiting,  \* kept_alive renews the lease for the whole job, help wait included (runs.py Leases.hold)
          FencedWrites,       \* writes check lease_run_id and fence (catalog/store.py fenced, scrapers/store.py _HELD)
          ConditionalExpire,  \* the agent's expiry is a CAS from a live status (help/store.py _EXPIRE)
          Stalls,             \* a worker may miss heartbeats until its lease lapses (GC pause, partition)
          Skew,               \* the server (done) and the worker (expiry) read different clocks
          MaxRestarts         \* agent/agent.py MAX_RESTARTS

None == "none"
Open == {"working", "waiting", "expiring", "stopping"}   \* states in which the run's Kernel browser exists

VARIABLES holder, ttl, fence, pc, mine, help, claimer, helpTtl, answer, acked, staleWrite, want, restarts, selfSkip

lease == <<holder, ttl, fence>>
desk == <<help, claimer, helpTtl, answer, acked>>
restart == <<want, restarts, selfSkip>>
vars == <<lease, pc, mine, desk, staleWrite, restart>>

TypeOK ==
    /\ holder \in Runs \cup {None}
    /\ ttl \in 0..LeaseTTL
    /\ fence \in 0..(Cardinality(Runs) * (MaxRestarts + 1))
    /\ pc \in [Runs -> {"queued", "working", "waiting", "expiring", "stopping", "restarting", "done"}]
    /\ mine \in [Runs -> 0..(Cardinality(Runs) * (MaxRestarts + 1))]
    /\ help \in [Runs -> {"none", "open", "claimed", "done", "expired"}]
    /\ claimer \in [Runs -> Helpers \cup {None}]
    /\ helpTtl \in [Runs -> 0..HelpTTL]
    /\ answer \in [Runs -> {"none", "helped", "unanswered"}]
    /\ acked \in [Runs -> BOOLEAN]
    /\ staleWrite \in BOOLEAN
    /\ want \in [Runs -> BOOLEAN]
    /\ restarts \in [Runs -> 0..MaxRestarts]
    /\ selfSkip \in BOOLEAN

Init ==
    /\ holder = None /\ ttl = 0 /\ fence = 0
    /\ pc = [r \in Runs |-> "queued"] /\ mine = [r \in Runs |-> 0]
    /\ help = [r \in Runs |-> "none"] /\ claimer = [r \in Runs |-> None]
    /\ helpTtl = [r \in Runs |-> 0] /\ answer = [r \in Runs |-> "none"] /\ acked = [r \in Runs |-> FALSE]
    /\ staleWrite = FALSE
    /\ want = [r \in Runs |-> FALSE] /\ restarts = [r \in Runs |-> 0] /\ selfSkip = FALSE

\* runs/store.py _CLAIM: lease_run_id IS NULL OR lease_expires_at <= now
Claim(r) ==
    /\ pc[r] = "queued" /\ (holder = None \/ ttl = 0)
    /\ holder' = r /\ ttl' = LeaseTTL /\ fence' = fence + 1
    /\ mine' = [mine EXCEPT ![r] = fence + 1] /\ pc' = [pc EXCEPT ![r] = "working"]
    /\ UNCHANGED <<desk, staleWrite, restart>>

\* app.py Jobs.carry_out: SourceBusy -> SKIPPED. A restarted run skipped over its own old lease lost its restart.
Skip(r) ==
    /\ pc[r] = "queued" /\ holder /= None /\ ttl > 0
    /\ pc' = [pc EXCEPT ![r] = "done"]
    /\ selfSkip' = (selfSkip \/ holder = r)
    /\ UNCHANGED <<lease, mine, desk, staleWrite, want, restarts>>

\* The browser closes inside the kept-alive work (agent.py Agent.run `async with opened`), so stopping still renews.
Renewing(r) == pc[r] \in {"working", "stopping"} \/ (RenewWhileWaiting /\ pc[r] \in {"waiting", "expiring"})

\* runs.py kept_alive beat -> Leases.renew; a failed renew raises LeaseLost and cancels the work
Renew(r) ==
    /\ Renewing(r)
    /\ IF holder = r /\ fence = mine[r] /\ ttl > 0
       THEN ttl' = LeaseTTL /\ UNCHANGED <<holder, fence, pc>>
       ELSE pc' = [pc EXCEPT ![r] = "stopping"] /\ UNCHANGED <<holder, ttl, fence>>
    /\ UNCHANGED <<mine, desk, staleWrite, restart>>

\* Time passes. Without stalls, an active holder's heartbeat always lands before its lease lapses.
Tick ==
    /\ ttl > 0
    /\ Stalls \/ ttl > 1 \/ holder = None \/ ~Renewing(holder)
    /\ ttl' = ttl - 1
    /\ UNCHANGED <<holder, fence, pc, mine, desk, staleWrite, restart>>

\* Versions.propose, Catalog.ingest, Healths.record, Accounts.save_*: one fenced UPDATE each
Write(r) ==
    /\ pc[r] = "working"
    /\ IF ~FencedWrites \/ (holder = r /\ fence = mine[r])
       THEN staleWrite' = (staleWrite \/ holder /= r)
       ELSE UNCHANGED staleWrite
    /\ UNCHANGED <<lease, pc, mine, desk, restart>>

\* agent/tools.py request_help -> HelpDesk.ask: insert OPEN; the agent only waits until the answer
Ask(r) ==
    /\ pc[r] = "working" /\ help[r] = "none"
    /\ help' = [help EXCEPT ![r] = "open"] /\ helpTtl' = [helpTtl EXCEPT ![r] = HelpTTL]
    /\ pc' = [pc EXCEPT ![r] = "waiting"]
    /\ UNCHANGED <<lease, mine, claimer, answer, acked, staleWrite, restart>>

HelpTick(r) ==
    /\ helpTtl[r] > 0 /\ help[r] \in {"open", "claimed"}
    /\ helpTtl' = [helpTtl EXCEPT ![r] = helpTtl[r] - 1]
    /\ UNCHANGED <<lease, pc, mine, help, claimer, answer, acked, staleWrite, restart>>

\* help/store.py _CLAIM: OPEN and not expired
TakeOver(h, r) ==
    /\ help[r] = "open" /\ helpTtl[r] > 0
    /\ help' = [help EXCEPT ![r] = "claimed"] /\ claimer' = [claimer EXCEPT ![r] = h]
    /\ UNCHANGED <<lease, pc, mine, helpTtl, answer, acked, staleWrite, restart>>

\* help/store.py _GIVE_BACK
GiveBack(h, r) ==
    /\ help[r] = "claimed" /\ claimer[r] = h
    /\ help' = [help EXCEPT ![r] = "open"] /\ claimer' = [claimer EXCEPT ![r] = None]
    /\ UNCHANGED <<lease, pc, mine, helpTtl, answer, acked, staleWrite, restart>>

\* help/store.py _DONE: claimed by this helper and not expired by the server's clock; the page then says it's
\* handed back. With skew the server can still see time left after the worker's clock ran out.
Done(h, r) ==
    /\ help[r] = "claimed" /\ claimer[r] = h /\ (helpTtl[r] > 0 \/ Skew)
    /\ help' = [help EXCEPT ![r] = "done"] /\ acked' = [acked EXCEPT ![r] = TRUE]
    /\ UNCHANGED <<lease, pc, mine, claimer, helpTtl, answer, staleWrite, restart>>

\* help/store.py _ANSWER (HelpQueue.say): a chat reply answers the request if it is open or this helper holds it; one
\* that someone else holds stays theirs, since they are driving the browser
Answer(h, r) ==
    /\ (help[r] = "open" \/ (help[r] = "claimed" /\ claimer[r] = h)) /\ (helpTtl[r] > 0 \/ Skew)
    /\ help' = [help EXCEPT ![r] = "done"] /\ claimer' = [claimer EXCEPT ![r] = h]
    /\ acked' = [acked EXCEPT ![r] = TRUE]
    /\ UNCHANGED <<lease, pc, mine, helpTtl, answer, staleWrite, restart>>

\* help.py HelpDesk._answer: read the request
Poll(r) ==
    /\ pc[r] = "waiting"
    /\ \/ help[r] = "done" /\ answer' = [answer EXCEPT ![r] = "helped"] /\ pc' = [pc EXCEPT ![r] = "working"]
       \/ help[r] = "expired" /\ answer' = [answer EXCEPT ![r] = "unanswered"] /\ pc' = [pc EXCEPT ![r] = "stopping"]
       \/ help[r] \in {"open", "claimed"} /\ helpTtl[r] = 0 /\ pc' = [pc EXCEPT ![r] = "expiring"] /\ UNCHANGED answer
    /\ UNCHANGED <<lease, mine, help, claimer, helpTtl, acked, staleWrite, restart>>

\* help/store.py _EXPIRE, then the loop reads again
Expire(r) ==
    /\ pc[r] = "expiring"
    /\ IF ~ConditionalExpire \/ help[r] \in {"open", "claimed"}
       THEN help' = [help EXCEPT ![r] = "expired"]
       ELSE UNCHANGED help
    /\ pc' = [pc EXCEPT ![r] = "waiting"]
    /\ UNCHANGED <<lease, mine, claimer, helpTtl, answer, acked, staleWrite, restart>>

\* The agent ends its turn; Jobs records the outcome
Finish(r) ==
    /\ pc[r] = "working"
    /\ pc' = [pc EXCEPT ![r] = "stopping"]
    /\ UNCHANGED <<lease, mine, desk, staleWrite, restart>>

\* agent/tools.py restart (Workspace.ask_restart, refused past MAX_RESTARTS), then the agent ends its turn
AskRestart(r) ==
    /\ pc[r] = "working" /\ restarts[r] < MaxRestarts
    /\ want' = [want EXCEPT ![r] = TRUE] /\ pc' = [pc EXCEPT ![r] = "stopping"]
    /\ UNCHANGED <<lease, mine, desk, staleWrite, restarts, selfSkip>>

\* browser.py Browsers.opened finally: delete the browser (saving its profile); then runs/store.py _RELEASE. A run
\* that asked to restart is left RESTARTING (Runs.restart), which the reaper skips.
Stop(r) ==
    /\ pc[r] = "stopping"
    /\ IF holder = r /\ fence = mine[r] THEN holder' = None /\ ttl' = 0 ELSE UNCHANGED <<holder, ttl>>
    /\ pc' = [pc EXCEPT ![r] = IF want[r] THEN "restarting" ELSE "done"]
    /\ UNCHANGED <<fence, mine, desk, staleWrite, restart>>

\* app.py Jobs.carry_out after the lease is released -> Queue.redeploy -> worker.py Deployer.carry_on: deploy `main`,
\* then dispatch the same run, which claims the source again and resumes its thread
Redeploy(r) ==
    /\ pc[r] = "restarting" /\ want[r]
    /\ pc' = [pc EXCEPT ![r] = "queued"]
    /\ want' = [want EXCEPT ![r] = FALSE] /\ restarts' = [restarts EXCEPT ![r] = restarts[r] + 1]
    /\ UNCHANGED <<lease, mine, desk, staleWrite, selfSkip>>

\* Deployer.carry_on: a failed deploy finishes the run FAILED
DeployFails(r) ==
    /\ pc[r] = "restarting"
    /\ pc' = [pc EXCEPT ![r] = "done"] /\ want' = [want EXCEPT ![r] = FALSE]
    /\ UNCHANGED <<lease, mine, desk, staleWrite, restarts, selfSkip>>

Agent(r) == Claim(r) \/ Skip(r) \/ Renew(r) \/ Write(r) \/ Ask(r) \/ Poll(r) \/ Expire(r) \/ Finish(r) \/ Stop(r)
            \/ AskRestart(r) \/ Redeploy(r) \/ DeployFails(r)
Person(r) == \E h \in Helpers : TakeOver(h, r) \/ GiveBack(h, r) \/ Done(h, r) \/ Answer(h, r)

\* Every run has ended: nothing left to do.
Finished == (\A r \in Runs : pc[r] = "done") /\ UNCHANGED vars

Next == \/ \E r \in Runs : Agent(r) \/ Person(r) \/ HelpTick(r)
        \/ Tick
        \/ Finished

Spec == Init /\ [][Next]_vars
\* Agents, the clocks and the poll make progress; people may never come.
Fairness == /\ \A r \in Runs : /\ WF_vars(Claim(r) \/ Skip(r)) /\ WF_vars(Finish(r)) /\ WF_vars(Poll(r))
                              /\ WF_vars(Expire(r)) /\ WF_vars(Stop(r)) /\ WF_vars(Renew(r)) /\ WF_vars(HelpTick(r))
                              /\ WF_vars(Redeploy(r) \/ DeployFails(r))
            /\ WF_vars(Tick)
LiveSpec == Spec /\ Fairness

\* A person and the agent never drive one browser at once.
OneDriver == \A r \in Runs : ~(pc[r] = "working" /\ help[r] = "claimed")
\* One browser per source profile: Kernel saves a profile when its browser is deleted, and a save replaces it.
OneBrowser == Cardinality({r \in Runs : pc[r] \in Open}) <= 1
\* No write lands for a run that no longer holds the source.
NoStaleWrite == ~staleWrite
\* A person told "handed back" never leaves the agent thinking nobody answered.
NoLostDone == \A r \in Runs : ~(acked[r] /\ answer[r] = "unanswered")
\* A restarted run never finds its own old lease in the way: it is redeployed only after letting go.
NoSelfSkip == ~selfSkip

\* Vacuity guards: each must be violated, proving the situation is reachable.
NotHelped == \A r \in Runs : answer[r] /= "helped"
NotUnanswered == \A r \in Runs : answer[r] /= "unanswered"
NotSecondHolder == fence < 2
NotRestarted == \A r \in Runs : restarts[r] = 0

AllDone == <>(\A r \in Runs : pc[r] = "done")
====
