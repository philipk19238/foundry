---- MODULE Runs_TTrace_1792637479 ----
EXTENDS Sequences, TLCExt, Runs_TEConstants, Toolbox, Naturals, TLC, Runs

_expression ==
    LET Runs_TEExpression == INSTANCE Runs_TEExpression
    IN Runs_TEExpression!expression
----

_trace ==
    LET Runs_TETrace == INSTANCE Runs_TETrace
    IN Runs_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        mine = ((r1 :> 1 @@ r2 :> 2))
        /\
        want = ((r1 :> FALSE @@ r2 :> FALSE))
        /\
        selfSkip = (FALSE)
        /\
        holder = (r2)
        /\
        helpTtl = ((r1 :> 0 @@ r2 :> 0))
        /\
        ttl = (2)
        /\
        restarts = ((r1 :> 0 @@ r2 :> 0))
        /\
        acked = ((r1 :> FALSE @@ r2 :> FALSE))
        /\
        staleWrite = (TRUE)
        /\
        help = ((r1 :> "none" @@ r2 :> "none"))
        /\
        pc = ((r1 :> "working" @@ r2 :> "working"))
        /\
        answer = ((r1 :> "none" @@ r2 :> "none"))
        /\
        claimer = ((r1 :> none @@ r2 :> none))
        /\
        fence = (2)
    )
----

_init ==
    /\ mine = _TETrace[1].mine
    /\ fence = _TETrace[1].fence
    /\ answer = _TETrace[1].answer
    /\ pc = _TETrace[1].pc
    /\ holder = _TETrace[1].holder
    /\ ttl = _TETrace[1].ttl
    /\ restarts = _TETrace[1].restarts
    /\ want = _TETrace[1].want
    /\ acked = _TETrace[1].acked
    /\ selfSkip = _TETrace[1].selfSkip
    /\ help = _TETrace[1].help
    /\ helpTtl = _TETrace[1].helpTtl
    /\ staleWrite = _TETrace[1].staleWrite
    /\ claimer = _TETrace[1].claimer
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ mine  = _TETrace[i].mine
        /\ mine' = _TETrace[j].mine
        /\ fence  = _TETrace[i].fence
        /\ fence' = _TETrace[j].fence
        /\ answer  = _TETrace[i].answer
        /\ answer' = _TETrace[j].answer
        /\ pc  = _TETrace[i].pc
        /\ pc' = _TETrace[j].pc
        /\ holder  = _TETrace[i].holder
        /\ holder' = _TETrace[j].holder
        /\ ttl  = _TETrace[i].ttl
        /\ ttl' = _TETrace[j].ttl
        /\ restarts  = _TETrace[i].restarts
        /\ restarts' = _TETrace[j].restarts
        /\ want  = _TETrace[i].want
        /\ want' = _TETrace[j].want
        /\ acked  = _TETrace[i].acked
        /\ acked' = _TETrace[j].acked
        /\ selfSkip  = _TETrace[i].selfSkip
        /\ selfSkip' = _TETrace[j].selfSkip
        /\ help  = _TETrace[i].help
        /\ help' = _TETrace[j].help
        /\ helpTtl  = _TETrace[i].helpTtl
        /\ helpTtl' = _TETrace[j].helpTtl
        /\ staleWrite  = _TETrace[i].staleWrite
        /\ staleWrite' = _TETrace[j].staleWrite
        /\ claimer  = _TETrace[i].claimer
        /\ claimer' = _TETrace[j].claimer

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("Runs_TTrace_1792637479.json", _TETrace)

=============================================================================

 Note that you can extract this module `Runs_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `Runs_TEExpression.tla` file takes precedence 
  over the module `Runs_TEExpression` below).

---- MODULE Runs_TEExpression ----
EXTENDS Sequences, TLCExt, Runs_TEConstants, Toolbox, Naturals, TLC, Runs

expression == 
    [
        \* To hide variables of the `Runs` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        mine |-> mine
        ,fence |-> fence
        ,answer |-> answer
        ,pc |-> pc
        ,holder |-> holder
        ,ttl |-> ttl
        ,restarts |-> restarts
        ,want |-> want
        ,acked |-> acked
        ,selfSkip |-> selfSkip
        ,help |-> help
        ,helpTtl |-> helpTtl
        ,staleWrite |-> staleWrite
        ,claimer |-> claimer
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_mineUnchanged |-> mine = mine'
        
        \* Format the `mine` variable as Json value.
        \* ,_mineJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(mine)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_mineModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].mine # _TETrace[s-1].mine
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE Runs_TETrace ----
\*EXTENDS IOUtils, Runs_TEConstants, TLC, Runs
\*
\*trace == IODeserialize("Runs_TTrace_1792637479.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE Runs_TETrace ----
EXTENDS Runs_TEConstants, TLC, Runs

trace == 
    <<
    ([mine |-> (r1 :> 0 @@ r2 :> 0),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> none,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 0,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> FALSE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "queued" @@ r2 :> "queued"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 0]),
    ([mine |-> (r1 :> 1 @@ r2 :> 0),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> r1,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 2,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> FALSE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "queued"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 1]),
    ([mine |-> (r1 :> 1 @@ r2 :> 0),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> r1,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 1,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> FALSE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "queued"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 1]),
    ([mine |-> (r1 :> 1 @@ r2 :> 0),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> r1,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 0,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> FALSE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "queued"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 1]),
    ([mine |-> (r1 :> 1 @@ r2 :> 2),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> r2,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 2,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> FALSE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 2]),
    ([mine |-> (r1 :> 1 @@ r2 :> 2),want |-> (r1 :> FALSE @@ r2 :> FALSE),selfSkip |-> FALSE,holder |-> r2,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 2,restarts |-> (r1 :> 0 @@ r2 :> 0),acked |-> (r1 :> FALSE @@ r2 :> FALSE),staleWrite |-> TRUE,help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),fence |-> 2])
    >>
----


=============================================================================

---- MODULE Runs_TEConstants ----
EXTENDS Runs

CONSTANTS r1, r2, h1, h2, none

=============================================================================

---- CONFIG Runs_TTrace_1792637479 ----
CONSTANTS
    Runs = { r1 , r2 }
    Helpers = { h1 , h2 }
    None = none
    LeaseTTL = 2
    HelpTTL = 2
    RenewWhileWaiting = TRUE
    FencedWrites = FALSE
    ConditionalExpire = TRUE
    Skew = TRUE
    Stalls = TRUE
    MaxRestarts = 1
    r1 = r1
    h2 = h2
    r2 = r2
    none = none
    h1 = h1

INVARIANT
    _inv

CHECK_DEADLOCK
    \* CHECK_DEADLOCK off because of PROPERTY or INVARIANT above.
    FALSE

INIT
    _init

NEXT
    _next

CONSTANT
    _TETrace <- _trace

ALIAS
    _expression
=============================================================================
\* Generated on Thu Oct 22 02:51:20 UTC 2026