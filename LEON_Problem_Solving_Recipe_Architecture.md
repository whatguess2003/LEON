# LEON — Meaning-Preserving Problem Solving & Recipe Architecture

**Status:** Proposed architecture for coding agent review  
**Principle:** **Preserve the question. Simplify the solution. A recipe is an executable method for solving a problem—not a list of ingredients.**

## 1. Why change the architecture?

Keyphrase extraction reduces a request to isolated terms and loses relationships, constraints, time semantics, units, and intended outputs. A low-reasoning agent then guesses how those fragments fit together. A catalog of SQL snippets alone does not fix this: knowing *which* snippets exist is different from knowing *how* to use them to solve a problem.

LEON should preserve the original question as the authoritative contract, produce a meaning-preserving problem specification, find or develop a **solution procedure**, execute it, and verify that the evidence answers the question.

## 2. What is a recipe?

A **recipe** is a reusable, versioned, validated **problem-solving procedure**. It states:

1. **Problem addressed:** what question class it answers, including definitions and boundaries.
2. **Preconditions:** data availability, schema capabilities, identity resolution, time semantics, and required permissions.
3. **Inputs:** typed parameters with defaults and validation rules.
4. **Algorithm:** ordered operations, dependencies, joins, calculations, filtering, aggregation, and fallback behavior.
5. **Implementation:** executable SQL or calls to other recipes, with explicit mappings between their inputs and outputs.
6. **Output contract:** exact fields, grain (guest/reservation/room/night/channel), units, and interpretation.
7. **Verification:** invariants, independent test cases, known limitations, and versioned dependencies.
8. **Failure semantics:** what to do if data is missing, ambiguous, contradictory, or a prerequisite fails.

A recipe is **not** `['arrivals', 'stay history', 'complaints']`. That is an ingredient list. A recipe says *which arrivals to retrieve, how to identify guests, which historical stays count, how complaints attach to those stays, how to combine records without duplication, and what result to return*.

## 3. High-level architecture

```text
USER / PMS EVENT / SCHEDULE
          |
          v
WORKFLOW CONTROLLER  <---->  EVIDENCE & TASK STATE STORE
          |
          v
DESK AGENT (understands original request; may plan simple composition)
          |
          v
CONTEXT ENGINE (task-specific retrieval and evidence packaging; no autonomous decisions)
          |
          v
MEANING-PRESERVING PROBLEM SPECIFICATION
          |
          v
RECIPE RESOLVER (semantic discovery + typed input/output compatibility)
          |
     +----+-------------------+
     |                        |
KNOWN SOLUTION          UNKNOWN / UNSAFE COMPOSITION
     |                        |
     v                        v
RECIPE EXECUTOR         CATALOG AGENT (high reasoning)
     |                        | develop, validate, generalize
     |                        v
     |                  VALIDATION / APPROVAL GATE
     |                        |
     |                  VERSIONED RECIPE LIBRARY
     |                        |
     +------------------------+ (available for future requests)
     |
     v
EVIDENCE + ANSWER CHECK -> DESK AGENT -> USER
```

The fast answering path **must not wait** for the Catalog Agent's background learning path. For a novel request, the Desk Agent may compose two or three existing recipes if the combination is simple and their contracts are compatible. Difficult or ambiguous composition escalates; it must not be guessed.

## 4. Responsibilities

| Component | Responsibility | Must not do |
|---|---|---|
| Workflow Controller | Trigger workflows, route actions, enforce budgets, retries, authorization, and idempotency | Invent business semantics |
| Context Engine | Retrieve and assemble relevant request, recipe, schema, and execution evidence; persist references | Approve publication or silently reinterpret user intent |
| Desk Agent | Preserve original question, resolve conversational intent, propose subproblems, orchestrate simple composition, synthesize answer | Treat missing evidence as facts or invent SQL joins |
| Recipe Resolver | Match a full problem specification to recipes using semantic relevance **and** typed compatibility | Rely on keyphrases alone |
| Recipe Executor | Bind parameters, execute validated read-only SQL and supported composition operators, capture provenance | Allow arbitrary unsafe SQL or opaque model-generated transformations |
| Catalog Agent | Research missing methods, construct complete procedures, validate, generalize, repair, propose publication | Approve its own unverified claims or change PMS data |
| Validation Gate / Publisher | Enforce security, tests, business-rule approvals, atomic versioned publication and indexing | Treat a successful SQL run as sufficient correctness proof |

A separate Query Agent is optional. The existing low-reasoning Query Agent can remain for bounded ad hoc SQL, but routine validated recipes should run directly through the executor. Avoid adding an LLM merely to select pages.

## 5. Meaning-preserving problem specification

Keep the **unaltered original question** alongside a structured specification. Do not replace it with a keyword bag or an oversimplified paraphrase.

```json
{
  "original_question": "Which guests checking in tomorrow have stayed before and complained during a previous stay?",
  "scope": {"hotel_id": "<resolved hotel>", "timezone": "Asia/Kuala_Lumpur"},
  "target": {"entity": "guest", "output_grain": "one row per guest"},
  "constraints": [
    {"fact": "confirmed_arrival", "date": "$tomorrow"},
    {"fact": "completed_prior_stay", "before": "$arrival_date"},
    {"fact": "complaint_linked_to_prior_stay", "before": "$arrival_date"}
  ],
  "relationships": [
    "arrival guest identity must match historical guest identity",
    "complaint must belong to an eligible previous stay"
  ],
  "output": ["guest_id", "guest_name", "arrival_reservation_ids", "prior_stay_count", "prior_complaint_count"],
  "unresolved": []
}
```

The example is illustrative, not an assertion that LEON currently stores complaint-to-stay links. If that linkage does not exist, the system must report the missing capability or evidence.

**Meaning preservation checks:** every user condition survives; no new condition is invented; time references are resolved with hotel timezone; metric units and output grain are correct; joins preserve identity and hotel scope; uncertain business terms remain explicitly unresolved. Semantic equivalence may require model judgment, but structural checks should be deterministic.

## 6. Recipe types

### 6.1 Atomic recipe

A complete reusable solution to one bounded problem, such as **count confirmed reservations for a hotel as of a specified point in time**. It contains executable read-only SQL, status definition, parameters, output grain, and tests. It is not merely a named SQL fragment.

### 6.2 Composite recipe

A complete procedure combining atomic or composite recipes. It must specify **execution order, input/output mappings, join keys, row grain, filtering, calculations, and failure handling**. Prefer a single validated SQL query with CTEs when that is simpler and more efficient than materializing intermediate results. The composition's logical method should remain explicit regardless of implementation.

### 6.3 Ephemeral plan

A one-off procedure assembled by the Desk Agent from two or three compatible recipes. It can answer the user immediately after verification. Save the execution evidence; the Catalog Agent may subsequently generalize and publish the method if worthwhile. **Do not automatically publish every rare question.**

## 7. Worked example: a recipe is a method, not ingredients

**User:** “Which guests arriving tomorrow have stayed before and previously complained?”

**Ingredients only (insufficient):** arrivals, prior stays, complaints.

**Complete method:**

1. Resolve hotel and tomorrow's local date.
2. Retrieve confirmed arrival reservations for that date, with stable guest identifiers. If guest identity is missing or ambiguous, mark affected rows unresolved.
3. For those guest IDs, retrieve completed stays **strictly before the upcoming arrival**, scoped to the same hotel unless the request specifies a wider scope.
4. Retrieve complaints **linked to those eligible historical stays**; a complaint by the same name or unrelated to a previous stay is not sufficient.
5. Join on stable guest ID **and hotel scope**, and on stay ID where complaints require stay linkage. Use an existence filter or pre-aggregation to avoid multiplying rows when a guest has several stays or complaints.
6. Retain only guests satisfying **all** conditions. Return one row per guest, plus supporting counts and reservation references.
7. Check that every returned guest has an eligible arrival, an earlier completed stay, and an associated prior complaint. Report unavailable history separately rather than treating it as no complaint.

**Illustrative composite recipe schema:**

```json
{
  "recipe_id": "returning_arrivals_with_prior_complaints",
  "version": 1,
  "problem": "Find arriving guests who completed a previous stay and complained during a previous stay",
  "inputs": {"hotel_id": "hotel_id", "arrival_date": "local_date"},
  "preconditions": ["stable_guest_id", "historical_stays", "complaint_to_stay_link"],
  "steps": [
    {"id": "arrivals", "invoke": "confirmed_arrivals", "with": {"hotel_id": "$hotel_id", "date": "$arrival_date"}},
    {"id": "history", "invoke": "completed_stays_for_guests", "with": {"hotel_id": "$hotel_id", "guest_ids": "$arrivals.guest_ids", "before": "$arrival_date"}},
    {"id": "complaints", "invoke": "complaints_for_stays", "with": {"hotel_id": "$hotel_id", "stay_ids": "$history.stay_ids"}},
    {"id": "qualify", "operator": "semi_join", "left": "arrivals", "right": "history", "on": ["hotel_id", "guest_id"]},
    {"id": "filter", "operator": "semi_join", "left": "qualify", "right": "complaints", "via": "history.stay_id = complaints.stay_id"},
    {"id": "result", "operator": "group", "source": "filter", "by": ["hotel_id", "guest_id"], "outputs": ["guest_id", "guest_name", "arrival_reservation_ids", "prior_stay_count", "prior_complaint_count"]}
  ],
  "output_grain": "guest",
  "on_missing_precondition": "report_unavailable_not_zero",
  "validation": ["hotel_isolation", "temporal_order", "stable_identity", "no_duplicate_guest_rows"]
}
```

This is a **conceptual contract**, not a drop-in executable schema. The executor must implement and type-check every operator and mapping. In particular, the `via` join and grouped outputs require a formally defined intermediate row model; never execute an underspecified recipe by letting the model improvise joins.

## 8. Rare-question execution: answer first, learn second

Example: “Which OTA channels earned more revenue this month than last month despite fewer bookings?”

**Desk Agent's bounded ephemeral plan:**

1. Run validated `revenue_by_channel(period)` for each month.
2. Run validated `booking_count_by_channel(period)` for each month.
3. Align results on stable channel ID and consistent hotel, currency, revenue recognition, cancellation treatment, and date basis.
4. Filter `revenue_current > revenue_previous AND bookings_current < bookings_previous`.
5. Return channel, both revenue figures, both counts, and evidence provenance.

The Desk Agent may compile this answer from the two recipe families without waiting for a specialized recipe. If contracts conflict or a required metric is unavailable, it must escalate or give a qualified partial answer.

**Learning event:** persist original question, canonical specification, chosen recipe versions, parameter bindings, transformation plan, intermediate result metadata, final answer, and checks. The Catalog Agent reviews the event and may publish a generalized composite recipe. Publication is not required for every one-off request.

## 9. Context Engine: inputs, triggers, and output

The **Workflow Controller** triggers work from user messages, PMS changes, schedules, or tool outcomes. The **Context Engine** is invoked when an agent or executor needs task-specific context. It does not monitor everything continuously and does not approve publication.

Example request:

```json
{
  "task_id": "TASK-118",
  "event_type": "user_message",
  "consumer": "desk",
  "purpose": "decompose_and_answer",
  "scope": {"hotel_id": "SANDBOX HOTEL", "timezone": "Asia/Kuala_Lumpur"},
  "question_ref": "request:118",
  "references": [],
  "max_tokens": 3000
}
```

**Context retrieval stages:** (1) original question and scope; (2) relevant recipe summaries and typed contracts; (3) full selected procedures and required definitions; (4) execution evidence and targeted schema discovery only if needed. Keep original question available at every stage. Persist investigation state outside the LLM conversation.

**Outcome triggers:**

- `recipe_match` → validate bindings and execute.
- `simple_composition_possible` → Desk Agent proposes bounded ephemeral plan; validate compatibility and execute.
- `missing_recipe` / `unsupported_composition` → Catalog Agent investigation (potentially asynchronous).
- `execution_failed` → bounded diagnosis; distinguish data, permission, timeout, and knowledge defects.
- `composition_succeeded_without_catalog` → capture reusable learning evidence.
- `candidate_validated` → apply publication approval policy.

Deduplicate learning events by canonical capability signature and active investigation, not by literal question wording.

## 10. Discovery: meaning plus contracts

The resolver should search full **problem descriptions**, not just keyphrases. Combine semantic retrieval (where available), PostgreSQL full-text/trigram retrieval, domain/entity filters, and deterministic compatibility checks. Rank candidate recipes using:

- Whether the recipe's stated problem covers the requested conditions.
- Whether required inputs are available and type-correct.
- Whether output grain and fields can satisfy the requested answer.
- Whether business definitions, hotel scope, and time semantics match.
- Whether the recipe's dependencies are validated and active.

A high similarity score is **not** proof that a recipe answers the question. When no recipe fully matches, return a capability gap rather than forcing a poor match.

## 11. Validation, approval, and publication

The Catalog Agent **proposes**. The deterministic Validation Gate checks SQL read-only safety, parameterization, hotel isolation, schema compatibility, operator contracts, dependency versions, test fixtures, duplicate counting, temporal logic, missing-data semantics, and regression behavior.

**Business correctness cannot be inferred solely from successful SQL execution.** New or changed business definitions and schema changes require human authorization. Routine read-only recipes may be auto-published only when sufficient independent correctness evidence exists and all required checks pass. Unresolved cases remain candidates or require human review.

The publisher atomically stores the versioned procedure and updates its discovery index. Do not rebuild the entire index on every read or publication. Record the exact versions of dependencies and provide rollback and revalidation when dependencies change.

## 12. Suggested persistent records

- `requests`: original user message, scope, timestamps, resolved references.
- `problem_specs`: structured meaning, ambiguities, provenance, validation status.
- `recipes`: stable IDs, descriptions, type, semantic contract, lifecycle status.
- `recipe_versions`: immutable procedure bodies, inputs/outputs, dependencies, test evidence.
- `recipe_index`: searchable summaries, embeddings if used, filters, version pointers.
- `execution_runs`: recipe IDs/versions, parameters, SQL references, outputs, errors, timings.
- `composition_plans`: ephemeral or published procedures and step mappings.
- `investigations`: active learning tasks, evidence refs, next action, deduplication key.
- `publication_decisions`: validator outcomes, human approvals where required, audit trail.

These are logical data structures; map them onto existing LEON tables where appropriate rather than blindly creating duplicates.

## 13. Execution and security requirements

- Validate and authorize hotel scope server-side; do not trust hotel IDs supplied by an LLM.
- Permit only approved read-only SQL for routine recipe execution, with database-level read-only privileges and statement timeouts.
- Parameterize inputs; reject unbound or incompatible parameters.
- Define typed composition operators; never use free-form LLM instructions as executable join logic.
- Track output grain and join cardinality to prevent duplicated revenue, bookings, or guest counts.
- Preserve raw evidence and separate it from agent interpretations.
- Treat absent data as unknown/unavailable, not zero.
- Cap ad hoc attempts and composition complexity; escalate when the Desk Agent cannot reliably solve the problem.
- Store complete evidence externally while keeping model context bounded and task-specific.

## 14. Migration from the existing LEON catalog

1. **Keep** existing PMS, PostgreSQL, catalog registry, index, trace storage, and publication infrastructure.
2. Extend catalog pages with `problem`, `preconditions`, typed `inputs`, typed `outputs`, `output_grain`, and validation metadata.
3. Treat existing SQL pages as **atomic recipes** only after confirming their complete problem-solving contracts.
4. Introduce a problem-specification step that always retains the original question and all constraints.
5. Upgrade retrieval to rank recipes by semantic coverage and typed compatibility rather than keyphrases alone.
6. Add a deterministic composition executor with a small explicit operator set (filter, join/semi-join, group, compare, sort, project).
7. Allow Desk Agent to combine 2–3 compatible recipes for uncommon questions; save evidence and escalate unsafe cases.
8. Let Catalog Agent develop **complete** missing procedures and validate them before publication.
9. Separate foreground answering from background learning; ensure duplicate investigations converge on one candidate.
10. Measure correctness, coverage, latency, and actual recipe reuse before expanding complexity.

## 15. Acceptance tests

1. Three paraphrases of the same arrival-count question resolve to the same recipe **without changing the unit counted**.
2. A request for guest headcount does not silently use a reservation-count recipe.
3. A three-condition guest-history question preserves all conditions and temporal relationships.
4. An existing recipe returns the answer without SQL regeneration.
5. A rare question is answered by a validated two-recipe composition without waiting for Catalog Agent publication.
6. A composition with incompatible output grain is rejected, not guessed.
7. Missing complaint history is reported as unavailable, not as zero complaints.
8. A successful ad hoc composition creates one deduplicated learning investigation.
9. Catalog Agent publishes a full executable method with tests, not merely a list of recipe IDs.
10. The next equivalent question reuses the published method and exact versioned dependencies.
11. A new business definition cannot be auto-approved solely because SQL executes.
12. Normal request-time reads do not rebuild or reseed the entire catalog index.

## 16. Architectural decision

**A recipe answers “How do we solve this class of problem correctly?”** It must be a sufficiently specified executable procedure with semantics, steps, and verification—not a bag of relevant facts or SQL page names.

**LEON's operating loop:**

> Preserve original meaning → Identify the problem → Reuse or construct a complete method → Execute with evidence → Verify the answer → Optionally generalize and publish the method.

The goal is not to maximize recipe count. The goal is to make the Desk Agent reliably solve hotel-management problems with minimal hallucination and progressively less repeated reasoning.
