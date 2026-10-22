---- MODULE Crawls ----
\* One crawl run's attempts: dispatches, workers that die or go quiet (alive, renewing any lease, not beating), the
\* person's restart, the reaper (revive / give up / reap), the source lease (refresh only) and the attempt folders
\* (renamed from the newest earlier attempt). Every action names the code it stands for.
EXTENDS Naturals, FiniteSets

CONSTANTS Workers,      \* one container per dispatch (worker/functions.py carry_out), each used once
          Refresh,      \* TRUE: a refresh (holds the source lease); FALSE: a trial (no lease)
          Most,         \* runs.py MOST_ATTEMPTS
          MaxRestarts,  \* how often the person presses restart
          MaxFaults,    \* worker deaths + quiet spells
          MaxQuiet,     \* of those, quiet spells (alive, still renewing, not beating)
          FencedFinish, \* fix: _FINISH also requires attempts = the finishing attempt
          FencedStart,  \* fix: _START requires QUEUED and attempts = the attempt read; otherwise the worker exits
          BusyWaits     \* fix: SourceBusy on a carried-on crawl leaves it to the reaper instead of finishing SKIPPED

None == "none"
Ended == {"succeeded", "failed", "skipped"}
MaxA == Most + MaxRestarts + 1
Names == 0..MaxA
MaxIno == MaxA + 2

VARIABLES status, attempts, age, pending, restarts, faults, quiets,
          pc, att, mode, proc, ino, holder, lage, names, nextIno,
          staleFinish, staleBeat, revivedOver, revived

run == <<status, attempts, age>>
env == <<pending, restarts, faults, quiets>>
wk == <<pc, att, mode, proc, ino>>
lease == <<holder, lage>>
dir == <<names, nextIno>>
ghost == <<staleFinish, staleBeat, revivedOver, revived>>
vars == <<run, env, wk, lease, dir, ghost>>

PCs == {"idle", "got", "claim", "take", "crawl", "ok", "stopped", "stalled", "busy", "done"}

TypeOK ==
    /\ status \in {"queued", "running"} \cup Ended
    /\ attempts \in 0..MaxA
    /\ age \in 0..3
    /\ pending \in 0..3
    /\ pc \in [Workers -> PCs]
    /\ att \in [Workers -> 0..MaxA]
    /\ mode \in [Workers -> {"alive", "quiet", "dead"}]
    /\ proc \in [Workers -> BOOLEAN]
    /\ ino \in [Workers -> 0..MaxIno]
    /\ holder \in Workers \cup {None}
    /\ lage \in 0..2
    /\ names \in [Names -> 0..MaxIno]

Init ==
    /\ status = "queued" /\ attempts = 0 /\ age = 0
    /\ pending = 1 /\ restarts = 0 /\ faults = 0 /\ quiets = 0
    /\ pc = [w \in Workers |-> "idle"] /\ att = [w \in Workers |-> 0] /\ mode = [w \in Workers |-> "alive"]
    /\ proc = [w \in Workers |-> FALSE] /\ ino = [w \in Workers |-> 0]
    /\ holder = None /\ lage = 0
    /\ names = [n \in Names |-> 0] /\ nextIno = 1
    /\ staleFinish = FALSE /\ staleBeat = FALSE /\ revivedOver = FALSE /\ revived = 0

Dispatched == pending' = IF pending < 3 THEN pending + 1 ELSE pending

\* runs/store.py _FINISH: only a run not yet ended (and, fixed, only the newest attempt)
Fin(w, s) ==
    IF status \notin Ended /\ (~FencedFinish \/ attempts = att[w])
    THEN /\ status' = s /\ staleFinish' = (staleFinish \/ attempts /= att[w])
    ELSE UNCHANGED <<status, staleFinish>>

Live(w) == mode[w] = "alive"
Beating(w) == Live(w) /\ pc[w] = "crawl" /\ status = "running" /\ attempts = att[w]

\* Time passes: an alive worker beats every POLL_SECONDS (a beating attempt keeps beat_at fresh, a superseded one
\* stops), and its other steps take seconds. age 1 = QUIET (1 min), 2 = LEASE (2 min), 3 = STALE (3 min).
Tick ==
    /\ age < 3
    /\ ~\E w \in Workers : Live(w) /\ pc[w] \notin {"idle", "done"}
    /\ age' = age + 1
    /\ UNCHANGED <<status, attempts, env, wk, lease, dir, ghost>>

\* A dead holder stops renewing (runs.py kept_alive); its lease lapses after LEASE. A quiet one still renews.
LeaseTick ==
    /\ holder /= None /\ mode[holder] = "dead" /\ lage < 2
    /\ lage' = lage + 1
    /\ UNCHANGED <<run, env, wk, holder, dir, ghost>>
LeaseLive == holder /= None /\ lage < 2

\* worker/functions.py carry_out on a fresh container; app.py Jobs.carry_out reads the run
Pick(w) ==
    /\ pc[w] = "idle" /\ pending > 0
    /\ pending' = pending - 1 /\ pc' = [pc EXCEPT ![w] = "got"] /\ att' = [att EXCEPT ![w] = attempts]
    /\ UNCHANGED <<run, restarts, faults, quiets, mode, proc, ino, lease, dir, ghost>>

\* runs/store.py _START (Runs.start)
Start(w) ==
    /\ pc[w] = "got" /\ Live(w)
    /\ IF FencedStart /\ ~(status = "queued" /\ attempts = att[w])
       THEN pc' = [pc EXCEPT ![w] = "done"] /\ UNCHANGED <<status, age>>
       ELSE /\ status' = "running" /\ age' = 0
            /\ pc' = [pc EXCEPT ![w] = IF Refresh THEN "claim" ELSE "take"]
    /\ UNCHANGED <<attempts, env, att, mode, proc, ino, lease, dir, ghost>>

\* runs/store.py _CLAIM (Leases.claim); SourceBusy otherwise
Claim(w) ==
    /\ pc[w] = "claim" /\ Live(w)
    /\ IF ~LeaseLive
       THEN holder' = w /\ lage' = 0 /\ pc' = [pc EXCEPT ![w] = "take"]
       ELSE pc' = [pc EXCEPT ![w] = "busy"] /\ UNCHANGED lease
    /\ UNCHANGED <<run, env, att, mode, proc, ino, dir, ghost>>

\* app.py Jobs._carry SourceBusy: a crawl has no agent, so it finishes SKIPPED
Busy(w) ==
    /\ pc[w] = "busy" /\ Live(w)
    /\ IF BusyWaits /\ att[w] > 0 THEN UNCHANGED <<status, staleFinish>> ELSE Fin(w, "skipped")
    /\ pc' = [pc EXCEPT ![w] = "done"]
    /\ UNCHANGED <<attempts, age, env, att, mode, proc, ino, lease, dir, staleBeat, revivedOver, revived>>

\* scrapers.py Crawls._taken_over, then the crawl process starts writing (listings/files opened once, append)
Earlier(a) == {n \in Names : names[n] /= 0 /\ n < a}
Take(w) ==
    /\ pc[w] = "take" /\ Live(w)
    /\ LET a == att[w] IN
       IF names[a] = 0 /\ Earlier(a) /= {}
       THEN LET n == CHOOSE m \in Earlier(a) : \A k \in Earlier(a) : k <= m IN
            /\ names' = [names EXCEPT ![a] = names[n], ![n] = 0] /\ ino' = [ino EXCEPT ![w] = names[n]]
            /\ UNCHANGED nextIno
       ELSE IF names[a] = 0
       THEN /\ names' = [names EXCEPT ![a] = nextIno] /\ nextIno' = nextIno + 1
            /\ ino' = [ino EXCEPT ![w] = nextIno]
       ELSE ino' = [ino EXCEPT ![w] = names[a]] /\ UNCHANGED dir
    /\ proc' = [proc EXCEPT ![w] = TRUE] /\ pc' = [pc EXCEPT ![w] = "crawl"]
    /\ UNCHANGED <<run, env, att, mode, lease, ghost>>

\* scrapers.py Crawls._watched: watch.beat (runs/store.py _BEAT); False -> CrawlStopped, terminate then kill
Beat(w) ==
    /\ pc[w] = "crawl" /\ Live(w)
    /\ IF status = "running" /\ attempts = att[w]
       THEN age' = 0 /\ staleBeat' = (staleBeat \/ attempts /= att[w]) /\ UNCHANGED <<pc, proc>>
       ELSE pc' = [pc EXCEPT ![w] = "stopped"] /\ proc' = [proc EXCEPT ![w] = FALSE] /\ UNCHANGED <<age, staleBeat>>
    /\ UNCHANGED <<status, attempts, env, att, mode, ino, lease, dir, staleFinish, revivedOver, revived>>

\* _watched: no request for `stall` -> CrawlStalled (the process is stopped)
Stall(w) ==
    /\ pc[w] = "crawl" /\ Live(w)
    /\ pc' = [pc EXCEPT ![w] = "stalled"] /\ proc' = [proc EXCEPT ![w] = FALSE]
    /\ UNCHANGED <<run, env, att, mode, ino, lease, dir, ghost>>

\* The crawl process exits; Crawls.live reports and removes the run's folder (rmtree root)
Exit(w) ==
    /\ pc[w] = "crawl" /\ Live(w)
    /\ pc' = [pc EXCEPT ![w] = "ok"] /\ proc' = [proc EXCEPT ![w] = FALSE]
    /\ names' = [n \in Names |-> 0]
    /\ UNCHANGED <<run, env, att, mode, ino, lease, nextIno, ghost>>

Release(w) == IF holder = w THEN holder' = None /\ lage' = 0 ELSE UNCHANGED lease

\* Refresher.refresh fenced writes, Jobs._refresh / _trial finish SUCCEEDED, then Leases.hold releases
Succeed(w) ==
    /\ pc[w] = "ok" /\ Live(w)
    /\ IF Refresh /\ holder /= w THEN Fin(w, "failed") ELSE Fin(w, "succeeded")
    /\ Release(w) /\ pc' = [pc EXCEPT ![w] = "done"]
    /\ UNCHANGED <<attempts, age, env, att, mode, proc, ino, dir, staleBeat, revivedOver, revived>>

\* Leases.hold finally releases, then Jobs._carry CrawlStopped: dispatch if the run is QUEUED
Stopped(w) ==
    /\ pc[w] = "stopped" /\ Live(w)
    /\ Release(w)
    /\ IF status = "queued" THEN Dispatched ELSE UNCHANGED pending
    /\ pc' = [pc EXCEPT ![w] = "done"]
    /\ UNCHANGED <<run, restarts, faults, quiets, att, mode, proc, ino, dir, ghost>>

\* Leases.hold finally releases, then Jobs._carry CrawlStalled: Queue.retry (_RETRY) or finish FAILED
Stalled(w) ==
    /\ pc[w] = "stalled" /\ Live(w)
    /\ Release(w)
    /\ IF att[w] + 1 < Most /\ status \in {"running", "failed"} /\ attempts = att[w]
       THEN /\ status' = "queued" /\ attempts' = attempts + 1 /\ age' = 0 /\ Dispatched
            /\ UNCHANGED <<staleFinish>>
       ELSE Fin(w, "failed") /\ UNCHANGED <<attempts, age, pending>>
    /\ pc' = [pc EXCEPT ![w] = "done"]
    /\ UNCHANGED <<restarts, faults, quiets, att, mode, proc, ino, dir, staleBeat, revivedOver, revived>>

\* runs.py Queue.restart: _RETRY with the attempt on the page, then dispatch now if quiet or failed
Restart ==
    /\ restarts < MaxRestarts /\ status \in {"running", "failed"} /\ attempts < MaxA
    /\ status' = "queued" /\ attempts' = attempts + 1 /\ age' = 0 /\ restarts' = restarts + 1
    /\ IF (status = "running" /\ age >= 1) \/ status = "failed" THEN Dispatched ELSE UNCHANGED pending
    /\ UNCHANGED <<faults, quiets, wk, lease, dir, ghost>>

\* runs.py Runs.reap: _REAP (queued crawls older than LEASE without a live lease), _REVIVE, _GIVE_UP; Queue.revive
Reap ==
    /\ status = "queued" /\ age >= 2 /\ ~LeaseLive
    /\ status' = "failed"
    /\ UNCHANGED <<attempts, age, env, wk, lease, dir, ghost>>
Revive ==
    /\ status = "running" /\ age = 3 /\ attempts < Most
    /\ status' = "queued" /\ attempts' = attempts + 1 /\ age' = 0 /\ Dispatched
    /\ revived' = revived + 1 /\ revivedOver' = (revivedOver \/ attempts + 1 > Most)
    /\ UNCHANGED <<restarts, faults, quiets, wk, lease, dir, staleFinish, staleBeat>>
GiveUp ==
    /\ status = "running" /\ age = 3 /\ attempts >= Most
    /\ status' = "failed"
    /\ UNCHANGED <<attempts, age, env, wk, lease, dir, ghost>>

\* A container dies (its crawl process with it), or a worker goes quiet and comes back
Die(w) ==
    /\ faults < MaxFaults /\ mode[w] /= "dead" /\ pc[w] \notin {"idle", "done"}
    /\ mode' = [mode EXCEPT ![w] = "dead"] /\ proc' = [proc EXCEPT ![w] = FALSE] /\ faults' = faults + 1
    /\ UNCHANGED <<run, pending, restarts, quiets, pc, att, ino, lease, dir, ghost>>
Quiet(w) ==
    /\ faults < MaxFaults /\ quiets < MaxQuiet /\ Live(w) /\ pc[w] = "crawl"
    /\ mode' = [mode EXCEPT ![w] = "quiet"] /\ faults' = faults + 1 /\ quiets' = quiets + 1
    /\ UNCHANGED <<run, pending, restarts, pc, att, proc, ino, lease, dir, ghost>>
Wake(w) ==
    /\ mode[w] = "quiet"
    /\ mode' = [mode EXCEPT ![w] = "alive"]
    /\ UNCHANGED <<run, env, pc, att, proc, ino, lease, dir, ghost>>

Work(w) == Pick(w) \/ Start(w) \/ Claim(w) \/ Busy(w) \/ Take(w) \/ Beat(w) \/ Stall(w) \/ Exit(w)
           \/ Succeed(w) \/ Stopped(w) \/ Stalled(w) \/ Wake(w)
Steps(w) == Start(w) \/ Claim(w) \/ Busy(w) \/ Take(w) \/ Succeed(w) \/ Stopped(w) \/ Stalled(w)

Settled == status \in Ended /\ \A w \in Workers : pc[w] \in {"idle", "done"} \/ mode[w] = "dead"
Idle == Settled /\ UNCHANGED vars

Next == \/ \E w \in Workers : Work(w) \/ Die(w) \/ Quiet(w)
        \/ Tick \/ LeaseTick \/ Restart \/ Reap \/ Revive \/ GiveUp
        \/ Idle

Spec == Init /\ [][Next]_vars
\* Workers' own steps, the clocks, the reaper and the beat make progress; a quiet worker comes back; a crawl that
\* ends at the latest when its container times out (functions.py RUN_HOURS), so Exit or Stall comes.
Fairness == /\ \A w \in Workers : /\ WF_vars(Steps(w)) /\ WF_vars(Pick(w)) /\ WF_vars(Beat(w)) /\ WF_vars(Wake(w))
                                   /\ WF_vars(Exit(w) \/ Stall(w))
            /\ WF_vars(Tick) /\ WF_vars(LeaseTick) /\ WF_vars(Reap) /\ WF_vars(Revive) /\ WF_vars(GiveUp)
LiveSpec == Spec /\ Fairness

Crawling(w) == proc[w]
\* (1) At most one live crawl process writes a given attempt folder (inode: a rename keeps open handles).
OneWriter == \A v, w \in Workers : v /= w /\ Crawling(v) /\ Crawling(w) => ino[v] /= ino[w]
\* (2) A superseded attempt never ends the run over the newer attempt.
NoStaleFinish == ~staleFinish
\* (2) A superseded attempt never beats (runs/store.py _BEAT matches the attempt).
NoStaleBeat == ~staleBeat
\* Two dispatches of one attempt never both crawl.
NoTwinAttempt == \A v, w \in Workers : v /= w /\ pc[v] = "crawl" /\ pc[w] = "crawl" => att[v] /= att[w]
\* (4) A refresh crawls only while it holds the source lease.
Exclusive == Refresh => \A w \in Workers : Crawling(w) => holder = w
\* (5) The reaper never carries a crawl past MOST_ATTEMPTS.
ReviveBound == ~revivedOver
\* A restarted or revived crawl is carried on, not skipped over its own older attempt's lease.
NoSkippedRetry == ~(status = "skipped" /\ attempts > 0)
\* (3) No crawl run is stuck unfinished: under fairness it eventually ends for good.
Settles == <>[](status \in Ended)

\* Vacuity guards
NotRevived == revived = 0
NotSuperseded == ~\E v, w \in Workers : pc[v] = "crawl" /\ pc[w] \in {"crawl", "take"} /\ att[v] < att[w]
====
