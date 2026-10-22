---- MODULE Runs_TTrace_1792631528 ----
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
        mine = ((r1 :> 2 @@ r2 :> 1))
        /\
        help = ((r1 :> "none" @@ r2 :> "none"))
        /\
        pc = ((r1 :> "working" @@ r2 :> "working"))
        /\
        answer = ((r1 :> "none" @@ r2 :> "none"))
        /\
        claimer = ((r1 :> none @@ r2 :> none))
        /\
        holder = (r1)
        /\
        helpTtl = ((r1 :> 0 @@ r2 :> 0))
        /\
        ttl = (2)
        /\
        staleWrite = (FALSE)
        /\
        fence = (2)
        /\
        acked = ((r1 :> FALSE @@ r2 :> FALSE))
    )
----

_init ==
    /\ holder = _TETrace[1].holder
    /\ ttl = _TETrace[1].ttl
    /\ staleWrite = _TETrace[1].staleWrite
    /\ helpTtl = _TETrace[1].helpTtl
    /\ answer = _TETrace[1].answer
    /\ help = _TETrace[1].help
    /\ fence = _TETrace[1].fence
    /\ pc = _TETrace[1].pc
    /\ claimer = _TETrace[1].claimer
    /\ mine = _TETrace[1].mine
    /\ acked = _TETrace[1].acked
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ holder  = _TETrace[i].holder
        /\ holder' = _TETrace[j].holder
        /\ ttl  = _TETrace[i].ttl
        /\ ttl' = _TETrace[j].ttl
        /\ staleWrite  = _TETrace[i].staleWrite
        /\ staleWrite' = _TETrace[j].staleWrite
        /\ helpTtl  = _TETrace[i].helpTtl
        /\ helpTtl' = _TETrace[j].helpTtl
        /\ answer  = _TETrace[i].answer
        /\ answer' = _TETrace[j].answer
        /\ help  = _TETrace[i].help
        /\ help' = _TETrace[j].help
        /\ fence  = _TETrace[i].fence
        /\ fence' = _TETrace[j].fence
        /\ pc  = _TETrace[i].pc
        /\ pc' = _TETrace[j].pc
        /\ claimer  = _TETrace[i].claimer
        /\ claimer' = _TETrace[j].claimer
        /\ mine  = _TETrace[i].mine
        /\ mine' = _TETrace[j].mine
        /\ acked  = _TETrace[i].acked
        /\ acked' = _TETrace[j].acked

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("Runs_TTrace_1792631528.json", _TETrace)

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
        holder |-> holder
        ,ttl |-> ttl
        ,staleWrite |-> staleWrite
        ,helpTtl |-> helpTtl
        ,answer |-> answer
        ,help |-> help
        ,fence |-> fence
        ,pc |-> pc
        ,claimer |-> claimer
        ,mine |-> mine
        ,acked |-> acked
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_holderUnchanged |-> holder = holder'
        
        \* Format the `holder` variable as Json value.
        \* ,_holderJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(holder)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_holderModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].holder # _TETrace[s-1].holder
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
\*trace == IODeserialize("Runs_TTrace_1792631528.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE Runs_TETrace ----
EXTENDS Runs_TEConstants, TLC, Runs

trace == 
    <<
    ([mine |-> (r1 :> 0 @@ r2 :> 0),help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "queued" @@ r2 :> "queued"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),holder |-> none,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 0,staleWrite |-> FALSE,fence |-> 0,acked |-> (r1 :> FALSE @@ r2 :> FALSE)]),
    ([mine |-> (r1 :> 0 @@ r2 :> 1),help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "queued" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),holder |-> r2,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 2,staleWrite |-> FALSE,fence |-> 1,acked |-> (r1 :> FALSE @@ r2 :> FALSE)]),
    ([mine |-> (r1 :> 0 @@ r2 :> 1),help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "queued" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),holder |-> r2,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 1,staleWrite |-> FALSE,fence |-> 1,acked |-> (r1 :> FALSE @@ r2 :> FALSE)]),
    ([mine |-> (r1 :> 0 @@ r2 :> 1),help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "queued" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),holder |-> r2,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 0,staleWrite |-> FALSE,fence |-> 1,acked |-> (r1 :> FALSE @@ r2 :> FALSE)]),
    ([mine |-> (r1 :> 2 @@ r2 :> 1),help |-> (r1 :> "none" @@ r2 :> "none"),pc |-> (r1 :> "working" @@ r2 :> "working"),answer |-> (r1 :> "none" @@ r2 :> "none"),claimer |-> (r1 :> none @@ r2 :> none),holder |-> r1,helpTtl |-> (r1 :> 0 @@ r2 :> 0),ttl |-> 2,staleWrite |-> FALSE,fence |-> 2,acked |-> (r1 :> FALSE @@ r2 :> FALSE)])
    >>
----


=============================================================================

---- MODULE Runs_TEConstants ----
EXTENDS Runs

CONSTANTS r1, r2, h1, h2, none

=============================================================================

---- CONFIG Runs_TTrace_1792631528 ----
CONSTANTS
    Runs = { r1 , r2 }
    Helpers = { h1 , h2 }
    None = none
    LeaseTTL = 2
    HelpTTL = 2
    RenewWhileWaiting = TRUE
    FencedWrites = TRUE
    ConditionalExpire = TRUE
    Skew = TRUE
    Stalls = TRUE
    none = none
    r2 = r2
    h2 = h2
    r1 = r1
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
\* Generated on Thu Oct 22 01:12:09 UTC 2026