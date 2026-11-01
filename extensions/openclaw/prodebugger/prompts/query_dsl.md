# Record SQL v6: compact queries and optional evidence fields

One read-only SQLite SELECT, including WITH, JOIN, UNION, conditions, grouping,
ordering and aggregates. Each case gets ONE query including errors. Compose targeted
element functions in that query to test a hypothesis AND its strongest rival.
There is no aggregate row, byte, source-count or function-count retrieval budget.

PREFERRED COMPACT FORM
Batch repeated element searches and merge heterogeneous evidence BY NAME. Do not
write a long UNION branch for every term/module or pad missing fields with NULL.

```sql
-- Purpose: compare clean-bowl assertions with independently recorded actions.
WITH claims AS (
 -- Retrieve targeted source excerpts across related mental modules in one call.
 SELECT source_id,step,module,text,source_kind
 FROM SEARCH_TEXT(('clean','bowl'),('memory','reflection','plan'))
), actions AS (
 -- Preserve action provenance and arguments without inventing source offsets.
 SELECT step,'action' AS module,span AS text,fid,args,source_kind
 FROM TRACK_ACTION(('take','put'))
)
-- Align by field name; source-only and Fact-only fields are optional.
SELECT * FROM claims
UNION ALL BY NAME SELECT * FROM actions
ORDER BY step,module;
```

- SEARCH_TEXT and FIND_CLAIMS accept a string or a parenthesized list of quoted
  strings in either of their first two arguments. TRACK_ACTION accepts a string
  or such a list of relation names. Bounds remain two scalar integer literals.
  Lists form the ordered Cartesian product, first argument outermost, equivalent
  to UNION ALL of scalar calls. Repeated/overlapping searches retain duplicate
  rows. Never use columns, expressions, nested lists or subqueries as arguments.
- UNION ALL BY NAME aligns columns by their aliases, preserving duplicates.
  UNION BY NAME aligns and deduplicates. Column order need not match across
  branches. Give computed fields meaningful AS names. WHERE, JOIN, CTE, ORDER BY,
  LIMIT and aggregates retain SQL semantics. Use aliases inside CTE SELECTs,
  rather than a column-name list after the CTE name.
- Compact queries return row_format=sparse/v1: each JSON row includes only its
  non-NULL fields. columns lists the available output names; it is not a list of
  mandatory keys in every row. Zero, false and empty strings are retained.
  A missing key means unavailable/unknown, never false or absence in the world.
  SQL predicates such as IS NULL still operate on aligned NULL values internally.
- A batch is atomic: an invalid member makes that entire batch unavailable.
  Independent UNION ALL branches can still be recovered under the existing
  failure rules. Never interpret the partial return as a complete search.
- Legacy scalar functions and positional UNION remain supported. Prefer the
  compact syntax in new queries. Keep SELECT * only for an already-targeted
  element or a CTE whose columns you intentionally chose; no full-log access.

Include concise English `--` comments in the SQL string itself. Begin with
`-- Purpose: ...` to state the question being tested, then explain each CTE/retrieval
branch and meaningful bounds, filters or joins in one short comment near that SQL.
These are query annotations, not asserted findings. Use actual newlines after line
comments; `#` is not a SQLite comment. Do not send annotations as a separate turn.
The original annotated SQL is preserved in `trace_queries.jsonl` under `query`
and in the tool-call log under `arguments.sql` for display and debugging.

HARD BOUNDARY: steps, modules, facts, fact_args, entities and edges are NOT SQL
tables. RAW_STEP is forbidden, even for one step, with selected columns, via a CTE,
or a join. No full-step/full-module text accessor exists. A query must use at least
one element function. Do not reconstruct the trajectory or dump whole modules.
Identify the entity, state, action type or precise claim you need first.

BASIC QUERY CHECKLIST
- query_context.step_range gives the actual original [1,last_step]. Omitting optional
  bounds searches the full trajectory, including BEFORE the ROUTE anchor (task Facts
  may use step 0). Use [1,anchor] for prior support; use later bounds only to test
  recovery. Legacy window lo/hi identifies the anchor, not SQL visibility.
- Use query_context.relation_names. In ALFWorld: examine/look -> look,
  move/put -> put, go -> go, open action -> open_action; open is the state relation.
  TRACK_ACTION('examine') is invalid. WebShop actions include search and click.
- Arguments to table functions are LITERALS (or the documented string lists), not columns/subqueries. CTEs can join
  already materialized results, but SOURCE(found.fid,'phrase') is unsupported.
  Use known anchor IDs or SEARCH_TEXT to retrieve a missing source directly.
  For a discovered claim, retrieve targeted peer-module excerpts using literal
  task terms, then JOIN on step or use WHERE step IN (SELECT step FROM claims).
  This expands evidence within the same query, without a second call or full steps.
  Independent evidence may use different wording; do not require every source to
  repeat the manager's exact phrase. Do not treat an empty phrase match as refutation.
- SQL text uses SINGLE quotes: SEARCH_TEXT('4T','plan',1,15). Escape an apostrophe
  by doubling it: SEARCH_TEXT('women''s','plan',1,15). Do not use double quotes for
  text, invent function/relation names, or use expressions as function arguments.
- Generic SEARCH_TEXT('search','plan',...) and SEARCH_TEXT('search','reflection',...)
  are forbidden. Use the actual disputed phrase, such as 'fit type', or use
  TRACK_ACTION('search',1,anchor) for search actions. Specific two-character codes
  containing one letter and one digit (4T, 2T) are allowed and match whole tokens.
  SEARCH_TEXT('Search','observation',t,t) is allowed ONLY for a WebShop observation
  whose complete original text is the single home-page label Search. It cannot
  retrieve richer observations or a range of steps using this generic label.
- Before calling, check every function's literal
  arguments and returned columns, and use UNION ALL only for independent evidence.
  One malformed branch can make a dependent join/aggregate unusable. Prefer simple
  documented syntax; the single query opportunity is consumed even on failure.
- SOURCE(fid,'phrase') searches that Fact's actual original field. Choose a phrase
  from the disputed claim; copying 'clean' into a shopping case yields no evidence.
  SEARCH_TEXT is a case-insensitive literal search, not semantic search. Search the
  right module: a memory's claim about a click is not itself a recorded action.
- Match the entity AND attribute: has(value=1) may just mean object_type=cup.
  Keep args, slot/side, source_kind and claim timing when comparing Fact rows.
  Product keywords on a listing may describe different products. Track the product
  ID and navigation/selection context; an available option is not a selected option.
- Functions materialize BEFORE outer SQL filters/joins/LIMIT. Pass meaningful step
  bounds into functions and prefer a specific claim over a broad common word.
  All matching sources are returned before explicit SQL filtering. Inspect function_scopes.

Element table functions (literal arguments only; bounds are original steps):
- FIND_ENTITY('ladle'): existing IDs matching a specific noun/ID substring.
  Columns: entity_id,kind,entity_index. Use IDs already in the anchor when possible.
- TRACK_ENTITY('portable:ladle_1'[,from_step,to_step]): related compact Facts.
  ABOUT is an alias. agent/self and intention entities are too broad and rejected.
- TRACK_STATE('portable:ladle_1','has'[,from_step,to_step]): recorded env/task
  assignments of ONE entity and ONE relation; inspect source_kind for derived checks.
- STATE_AT('portable:ladle_1','has',7): latest assignments for that entity/relation.
  Missing state is UNKNOWN; this is recorded knowledge, not complete world state.
- TRACK_ACTION('search'[,from_step,to_step]): only original action facts of that
  operation, e.g. compare search strings across time without retrieving logs.
- TRACE_FACT(123,'backward'|'forward'|'goal',depth): provenance neighborhood;
  depth 0..8, default 2, no node-count cap. These edges are not causal proof.
- VERSIONS(123): Facts with the same relation and functional key.
- SOURCE(123,'specific phrase'): exact source excerpt around a specific phrase in that
  Fact's original field. Raw module Facts expose IDs, not their text: use this
  function to check what an extraction actually meant.
- SEARCH_TEXT('size medium','memory'[,from_step,to_step]): search one original
  field independently of parsing. Field is observation/memory/reflection/plan/action.
  Recorded OpenClaw sessions additionally support `message`: exact public user
  and assistant text, including final answers. `SOURCE(fid,needle)` on a message
  Fact returns this same unescaped recorded text with module=message and offsets
  relative to `session.events[step-1].text`. Messages are not internal memories.
  TRACE may assess module=message only when benchmark_scope permits it and a
  recorded assistant message has existing mind-side Facts at that event step.
  Use this to find evidence a parser missed. Literal 3..120-character search term
  (also specific two-character letter+digit codes, matched as whole tokens);
  no wildcards, generic labels, or empty terms. 'clean', a product ID, or an exact
  disputed claim are appropriate. No match means only no literal match.
- GOAL_STATUS('req:size',9): goal_id,step,state,details. Missing verification is
  unknown; unmodeled goals unresolved. Derived decision status is not world truth.
- COVERAGE(9): step,details. Extraction gaps and interpretive limits through step 9.
- FIND_CLAIMS('verification','reflection'[,from_step,to_step]): discover original
  clauses about one topic in one mental module (memory/reflection/plan). Topics:
  completion, verification, availability, selection, result_assessment,
  expected_gain, hypothesis, state, decision_basis. Same columns as SEARCH_TEXT.
  This is lexical topic discovery, NOT exhaustive semantic search or a fault test.
  Add entity filters and independent action/observation evidence where relevant.
  It finds clauses that have no typed memory Fact; no match still means unknown.

Compact Fact columns (TRACK_ENTITY/STATE/ACTION, STATE_AT, TRACE_FACT, VERSIONS):
fid,step,rel,args,slot,side,value,value_type,span,purpose,source_kind,parse_status,value_clipped,assertion_time,claim_scope,claim_polarity.
args is a JSON array of entity IDs. Raw memory/reflection/plan wrapper Facts have
NULL value/span to prevent paragraph dumping. Other text values/spans cap at 240
characters; value_clipped indicates value clipping. Query original source for quotes.
memory slot=mem, reflection=refl, plan=plan, action=act, observation=obs.
env/chk is a DERIVED check, not an observed state. Agent claims remain unverified.

SOURCE/SEARCH_TEXT/FIND_CLAIMS columns:
source_id,fid,step,module,start,end,text,matched,source_kind,parse_status.
text is an exact original slice, maximum 360 characters, with [start,end) offsets.
SEARCH_TEXT returns every matching source in the requested range; function_scopes
reports matched and returned counts. No shared character budget drops later branches.
Search bounds and terms must focus on a causal question; don't request every field.

Source-bound statement cards:
The response also has agent_statements for returned mental text and already-visible
anchor/assigned evidence. There is no mandatory all-fields row template. Return
step,module,text (or exact fid,span) when you want source-aligned mental statements;
the parser reads only present non-null fields. Omit unrelated attributes rather
than writing NULL placeholders. Preserve source_id and/or offsets to disambiguate repeated
identical clauses. Intermediate hits removed by WHERE/LIMIT do not produce cards.
Statements preserve original quote, polarity, modality, subjects/details and literal
qualifiers. Links express the author's stated reasons/conditions/expectations, not
verified evidence support. Partial clauses are never expanded from hidden text.
References can bind pronouns to exact earlier already-visible clauses in the same
module. They establish language reference only, not world truth or causal support.
Unresolved visible clauses are batched into one logical parsing pass after your query;
cached statements are reused. Truncated parser responses may be recovered for missing
units only, without new sources or an additional investigation/query turn.
model_reviewed means source-validated model extraction, not guaranteed semantic
completeness or world truth. Always compare the accompanying original quote.

Example pattern (adapt the topic, entity phrase and modules to the evidence):
```sql
-- Purpose: compare verification language in peer modules for the same object.
WITH checks AS (
 -- Discover reflection clauses about verification, retaining source provenance.
 SELECT * FROM FIND_CLAIMS('verification','reflection') WHERE lower(text) LIKE '%drawer%'
), plans AS (
 -- Inspect the related drawer decision only at discovered verification steps.
 SELECT * FROM SEARCH_TEXT('drawer','plan')
)
SELECT * FROM checks
UNION ALL BY NAME SELECT * FROM plans WHERE step IN (SELECT step FROM checks)
ORDER BY step,module;
```
This pattern exposes differences in what modules claim; it does not itself prove
that verification happened, was needed, or was improperly skipped.

Metadata tables may be joined to element results:
- goals(goal_id,name,count,of_kind,rel,target,status_mode)
- constraints(goal_id,property,operator,expected,value_type,unit,mode,span)
- route_evidence(candidate_id,issue_step,fid,hypothesis,goal_links)
- parse_audits(source_id,step,kind,status,template,reasons,unresolved)
- relations(rel,arity,functional_key)
Metadata text is bounded. JSON uses json_extract; constraints preserve the contract,
not proof that it was violated. Unmentioned terms in a parsed plan do not mean ignored.

REAL SOURCE EXAMPLES
These queries were checked against archived original trajectories offline. They teach retrieval, not benchmark answers or fault labels.
Use the current case's phrases, IDs and bounds; never copy an example mechanically.
Result notes describe source support and limits, not a required diagnosis.

Example 1: expand a memory investigation to related modules, actions and observations.
At step 12 the memory says: “I have opened drawer 1 and drawer 2 but did not find
a credit card (Observation 7, 10).” The trajectory has 30 steps. Check whether the
drawer-2 portion is supported; historical opening is not a claim of current location.
<!-- real-example: GPT-4o_012_alfworld_task_012 anchor=12 -->
```sql
-- Purpose: verify the drawer-2 memory against opening feedback and possible parsing errors.
WITH claims AS (
  -- Search earlier memories through step 12, preserving the original text and module.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('drawer 2', 'memory', 1, 12)
), peers AS (
  -- Inspect the related drawer reasoning and intended next action at discovered claim steps.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('drawer', ('reflection', 'plan'), 1, 12)
), observations AS (
  -- Verify the same drawer in original observations; a room listing does not prove opening.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('drawer 2', 'observation', 1, 12)
), attempts AS (
  -- Keep open_action attempts for drawer:2; an attempted action does not prove success.
  SELECT
  step, 'action' AS module, span AS text, source_kind
  FROM TRACK_ACTION('open_action', 1, 12)
  WHERE
    JSON_EXTRACT(args, '$[0]') = 'drawer:2'
)
-- Compare claims, actions and feedback by original step; Observation numbers are not Fact IDs.
SELECT
*
FROM claims
UNION ALL BY NAME
SELECT
*
FROM peers
WHERE
  step IN (
    SELECT
    step
    FROM claims
  )
UNION ALL BY NAME
SELECT
*
FROM observations
UNION ALL BY NAME
SELECT
*
FROM attempts
ORDER BY
  step,
  module;
```
Observed return: memory 12 and its related reflection/plan excerpts, action 9,
observations 1/9/10. Peer steps are discovered by SQL, not guessed in advance.
The memory keyword does not need to occur in the peer modules. Observation 9 says
drawer 2 is closed; observation 10 says it opens and contains a watch. The memory
has supporting history. Neither the closed state at 9 nor a different current
location at 12 establishes a contradiction. No whole step is retrieved.

Example 2: separate placement evidence, cleanliness knowledge and completion claims.
The task is “put a clean cup in sidetable.” Step 19 repeats that this goal was
achieved in Observation 11. Search earlier reflection and independent state support.
<!-- real-example: GPT-4o_010_alfworld_task_010 anchor=19 -->
```sql
-- Purpose: trace earlier completion claims and independently verify placement and cleanliness.
WITH claims AS (
  -- Search earlier completion assertions, including claims preceding the repeated anchor claim.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('has been completed', 'reflection', 1, 19)
), placement AS (
  -- Check placement using movement feedback; movement alone does not establish cleanliness.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('You move the cup 1', 'observation', 1, 19)
), cleanliness AS (
  -- Bind the same cup and clean attribute; unrelated true properties do not establish clean state.
  SELECT
  step, 'state' AS module, span AS text, source_kind
  FROM TRACK_STATE('portable:cup_1', 'has', 1, 19)
  WHERE
    JSON_EXTRACT(args, '$[1]') = 'attribute:clean'
)
-- Preserve each evidence source; no clean row means unknown, not that the cup is dirty.
SELECT
*
FROM claims
UNION ALL BY NAME
SELECT
*
FROM placement
UNION ALL BY NAME
SELECT
*
FROM cleanliness
ORDER BY
  step,
  module;
```
Observed return: reflection 11/12/15/18 and placement observation 11; no clean
assignment. The earlier reflection quote is available for attribution if justified.
The query establishes placement and repeated completion claims, not dirty state
or a unique causal fault. Check parsing gaps and the actual assertion's support.

Example 3: verify a memory of size selection against recorded clicks.
The requested henley has color 159- yellow and size x-large. At step 5 the memory
says: “The action "click[159- yellow]" confirms the color selection, while
"click[x-large]" verifies the size.” Product B09R9YCM6R was opened at step 2.
<!-- real-example: Qwen3-8B_002_chat_b000_t00_e01-7ab08a46 anchor=5 -->
```sql
-- Purpose: verify the claimed size click and distinguish available options from selected size.
WITH clicks AS (
  -- Track click elements for product navigation, color selection and purchase.
  SELECT
  step, 'action' AS module, CAST(value AS TEXT) AS text, source_kind
  FROM TRACK_ACTION('click', 1, 5)
), claims AS (
  -- Retrieve the disputed action claim verbatim; memory does not prove an action occurred.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('click[x-large]', 'memory', 1, 5)
), options AS (
  -- Steps 3 through 5 follow this product's opening at 2; retrieve only size-related excerpts.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('x-large', 'observation', 3, 5)
)
-- Check whether the claimed click precedes purchase; option text is not click evidence.
SELECT
*
FROM clicks
UNION ALL BY NAME
SELECT
*
FROM claims
UNION ALL BY NAME
SELECT
*
FROM options
ORDER BY
  step,
  module;
```
Observed clicks: product ID at 2, 159- yellow at 3 and 4, buy now at 5. The memory
quote and page option x-large are returned. No x-large click is in these retrieved
action elements. Page parsing is unresolved, so selected/default size is not settled
by the option excerpt. Evaluate the memory's claim separately from product state;
do not manufacture a prohibition on purchase from the words “Find me”.

Example 4: compare an “initial search” memory with the actual search sequence.
At step 22 a shirt-shopping memory calls a query containing “polyester or cotton
heather” the initial search. The original task includes fit type women. Compare
the actual searches and explicit plans; omitted query keywords alone are insufficient.
<!-- real-example: GPT-4o_003_chat_b000_t00_e02-1e119842 anchor=22 -->
```sql
-- Purpose: verify the initial-search claim and fit-type planning changes, including recovery.
WITH searches AS (
  -- Track original search arguments from step 1; the late anchor does not hide earlier actions.
  SELECT
  step, 'action' AS module, CAST(value AS TEXT) AS text, source_kind
  FROM TRACK_ACTION('search', 1, 22)
), claims AS (
  -- Find the exact initial-search claim; the memory itself does not verify historical actions.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('initial search query', 'memory', 1, 22)
), intentions AS (
  -- Retrieve fit-type plans to distinguish changed requirements, exploration and recovery.
  SELECT
  step, module, text, source_kind
  FROM SEARCH_TEXT('fit type', 'plan', 1, 22)
)
-- Compare actions, memories and plans with recovery evidence; earlier time alone is not causality.
SELECT
*
FROM searches
UNION ALL BY NAME
SELECT
*
FROM claims
UNION ALL BY NAME
SELECT
*
FROM intentions
ORDER BY
  step,
  module;
```
Observed return: search actions 1/4/12/22, memory 22, plans 4/12/22. The query
described as initial matches action 12 rather than action 1. Plan 12 discusses
adjusting fit type; plan 22 explicitly includes women again. These are distinct
memory/plan questions with recovery evidence, not a rule that every narrowed
search is wrong. Establish material causal effect before returning an error.

No aggregate output or per-function row/source-count caps are imposed. SQL LIMIT,
filters, graph depth and phrase-centered excerpt selection still restrict coverage. Neither
truncated=false nor an empty result proves exhaustive evidence. Full parse warnings
and source semantics accompany every result, even an error. Keep aliases for fid and
step truthful. Explicitly alias UNION output columns before ORDER BY.

RECOVERY IS LIMITED AND VISIBLE
The engine can normalize a double-quoted identifier to a string only in a known
table-function string-argument position; it records the exact repair in repairs.
It does not rewrite SELECT identifiers, invent search terms, widen bounds or retry
with a model. Prefer correct single quotes instead of relying on normalization.
If a known function has invalid arguments, independent top-level UNION ALL arms
may still return evidence. Every arm depending on that failure, including through
a CTE, join, COUNT or NOT EXISTS, is suppressed as a whole. Inspect partial,
function_errors, skipped_branches and function_scopes BEFORE interpreting rows.
Suppressed evidence is UNAVAILABLE, never zero matches, count=0 or refutation.
If no independent arm remains, the query fails. Full-log access, RAW_STEP, writes,
unknown functions still fail the whole query. Execution timeout and read-only checks
remain active. All recovery stays inside the same one query without widening scope.

Before reporting error, verify the exact original assertion and its temporal scope;
check whether partial extraction, an unmodeled attribute, or limited retrieval would
produce the same apparent violation. Use uncertain if that alternative remains
material. Do not infer world absence, bad agency or causality from missing records.
# Interpreting agent claims

Fact rows may include `assertion_time`, `claim_scope`, and `claim_polarity`.
`step` is the original utterance step; `assertion_time` is the step its claim
describes. A historical location is not a current location. Null assertion time
means temporal binding is unresolved. These fields do not certify the truth or
completeness of a memory. Missing typed memory/plan bindings remain unknown;
retrieve the particular source claim when semantic interpretation is required.
