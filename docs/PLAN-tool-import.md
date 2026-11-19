PLAN -- the Tool Import wizard: each row a decision a person can make.

**Status: plan only, nothing built (2026-11-18).** Jarrod reads the shape
before any slice starts. The spec is `docs/roadmap.md`'s entry "The Tool
Import wizard: each row a decision a person can make": Jarrod's request,
his four decisions, the Heimdal legend and the vocabulary. This plan does
not restate them. It reports what changed since the entry was written
(2026-11-02) and designs what the entry leaves open. Code was read at
`8381328`. Live counts were read on 2026-11-18 and are counts only.

---

## 1. What `_run_loop` does today

The entry said to read it again first, because the risk assessment is
only as true as this answer. It has changed twice since 2026-11-02: #81
(activation asks about a judged status) and #119 (responsibility a person
set).

**Which rows count.** The tenant's pinned baseline version, minus every
row that is `customer_owns` or `platform_only` (`engine.py:1084`).
`provider_satisfies` and `shared` remain, under `customer_system` or
`assists`, treated alike. Unchanged.

**Per objective the product newly joins:**
- **Status** (`assessment.contributor_added_status`):
  - **First contributor, status `not_met` or `pending_evidence`:**
    `pending_evidence`.
  - **First contributor, a judged status** (`met`, `partial`,
    `not_applicable`, `inherited`): the person's answer on the activation
    screen. On every path where nobody is asked, `needs_review`.
  - **A later contributor:** the status is left alone.
  - **`met`:** never set.
- **Responsibility** (`engine._tool_responsibility`): the tool's value is
  `resolve_contributor_responsibility` over every contributor's
  classification, with `shared` beating `provider_satisfies`.
  - **Set by a person** (`responsibility_set_by` not null): activation
    asks, defaulting to keep. A path nobody is asked in keeps the
    person's value and records the tool's.
  - **Not set by a person: written at once, nobody asked.** This is the
    path that matters below.
- **Records:** a `control_state_history` row naming the product, and an
  audit row for each judged objective.
- **Evidence tasks** (`_fanout_evidence_tasks`): fanned out from the
  rows' evidence specs.
- **A stale or unanswered ask** refuses the whole activation:
  `ActivationJudgementsChanged`, "Nothing was activated".

**Other writers of responsibility, for completeness:**
- **Deactivation:** if other contributors remain, responsibility is
  recomputed from them, keeping a person's value. **If this was the last
  contributor, responsibility is left as it was** (`engine.py:1515`).
- **A version move:** keeps a person's value and lists it in
  `responsibility_kept`.
- **`start_assessment`:** runs the loop once per active product, with
  nobody asked.

**SPRS.** `pending_evidence` and `needs_review` deduct exactly as
`not_met` does, so activation never raises the score.

## 2. The risk, re-assessed

**The remaining exposure, and the one this plan is built around:**
responsibility is written at activation, with nobody asked, on every
objective whose responsibility no person has set. It prints in
`ssp/05_customer_responsibility_matrix.html` and the consolidated SSP
PDF, for every objective at every status (`bundle_service.py`, the
`crm_rows` built from every objective). So a wrong `provider_satisfies`
row reaches an assessor-facing document the moment its tool goes active.
- **On live:** 956 of 960 control states have no hand-set responsibility.
  The brief said all 960. Four were set by hand on Jarrod's walk the
  same day, so the brief's figure was true when written.
- **Deactivating the tool does not undo it.** A last contributor's
  departure leaves the tool's responsibility in place (§1), so a wrong
  `provider_satisfies` outlives the tool that wrote it, until a person
  sets the value.

**What has shrunk since the entry was written:**
- **A status a person set** is asked about before a tool changes it
  (#81). A wrong row can no longer silently overwrite one.
- **A responsibility a person set** is asked about before a tool changes
  it (#119).
- **A person can set responsibility directly** (#119), so a wrong value
  is correctable per objective without touching the mapping.
- **So the blast radius of a wrong bulk-accepted row is:**
  - **unset responsibility,** written and printed (the exposure above);
  - **unjudged status** (`not_met`) moved to `pending_evidence`. That is
    visible, deducts the same, and creates evidence tasks a person will
    question;
  - **evidence tasks** asked of the MSP for coverage it does not provide.
    That is noise, but visible.

  It no longer reaches a judgement a person recorded.

**"At scale" is unchanged.** A baseline is reference data shared across
orgs. On live: 4 products, 103 rows in current versions, 5 active
activations. 5 of those rows are `provider_satisfies` with
`customer_system`, the highest-consequence combination.

**Decided (Jarrod, 2026-11-18): close the path; don't label it.**
Activation asks before writing `provider_satisfies` onto an
unset responsibility. **It asks once per activation, not once per row.**
Asking about hundreds of objectives one at a time would be a wall, not
consent. The ask:
- **One summary:** "This will record Provider for 38 objectives",
  with the list expandable and one confirm.
- **It reuses the activation dialog,** the one that already asks about
  judged statuses and hand-set responsibilities, with the same "changed
  since you were shown it" refusal. It does not invent a second pattern.
- **It applies to every activation,** whatever route its mapping row came
  from. The question is what the tenant's matrix will say, not how the
  row was made.

**With that in place, bulk accept's remaining danger is small:**
- A wrong `provider_satisfies` row reaches the matrix only after a
  person confirms the summary that lists it.
- A wrong coverage basis is never bulk-accepted (§5.4).
- What is left is a person confirming without reading. That is a
  diligence risk, not a silent one.

This is slice A.1 (§7). It changes activation for every tenant, so it is
its own slice, not part of the wizard's. Two parts are still open (§9):
whether `shared` is asked too, and what a path that asks nobody does.

## 3. Where the entry is stale or incomplete

1. **Ten columns, not eleven.** `candidate_state` was removed with
   migration 0072. The table today: Control(s), Source, Classification,
   Note, Provider contribution, Customer action, Scope note, Coverage
   basis, Objectives, Evidence (`ToolImportWizard.tsx:656`).
2. **There are two modes, and three ways in** (`Mode = "yaml" |
   "documents"`):
   - **yaml:** upload a finished file. Its review is **not the
     ten-column table**. It is the existing-mapping diff, four columns:
     Control, Change, Classification, Coverage basis
     (`ToolImportWizard.tsx:1026`). For a new product every row reads
     `added`.
   - **documents:** upload the CRM and/or baseline document. The model
     drafts rows (`importers/document.py`) into the editable ten-column
     table.
   - **Edit Baseline Mapping:** `editProductId` from the tool's page.
     This loads the current mapping into the same documents-mode table,
     and is the only route to web research (`importers/research.py`).
3. **Today the model never fills in coverage basis, by design.**
   `baseline.py:ControlEntry.coverage_basis` says the AI pipeline leaves
   it unset because "there's nothing in a vendor doc that reliably
   distinguishes the two without a human who knows the deployment", and
   apply refuses until a person sets it. A per-row recommendation
   reverses that rule. **Decided (Jarrod, 2026-11-18): the model
   recommends coverage basis, and a recommended coverage basis is never
   bulk-accepted.** It is the field the Heimdal example is about ("on
   whose systems"), and vendor documentation answers exactly that.
   Withholding the recommendation there would withhold it where it is
   most useful. The safety comes from the asymmetry, not from silence:
   - **Every model-recommended coverage basis goes to individual review,**
     whatever its citation.
   - **Why:** recommending `platform_only` when the truth is
     `customer_system` loses coverage, which is visible. The other
     direction claims coverage the vendor never provides, in a document
     an assessor reads.

   The half of the old rule that mattered stands: a recommendation is
   never a default, and never written unless a person takes it.
4. **The provenance precedent can be forged.** `Product.ai_generated_at`
   and `ai_generated_model` reach the database from keys in the YAML the
   client submits at apply (`routers/admin_products.py:756`). The
   documents route submits YAML too. A hand-edited file can set, change
   or omit them. Following that precedent at row level would let a file
   claim "accepted individually by Jarrod". So row provenance is written
   by the server, from a record the server holds (§5.6). The product-level
   fields are left as they are, and noted.

## 4. The two modes, and what each review is

| Way in | Arrives with | Recommendation | Bulk accept | Review |
|---|---|---|---|---|
| documents, new product | the model's proposal | yes, every row | yes, except held back | the new row table (§5.1) |
| Edit Baseline Mapping, with research | current values plus research | yes, where research or the model speaks to a row | yes, except held back; a changed value is always held back (H4) | the row table, each row showing current and recommended |
| Edit Baseline Mapping, no research | current values | none | nothing to accept | the row table, as an editor |
| yaml | the author's own values | **none** | **none** | the diff, with the questions and legend |

**yaml has no recommendation column.** The file's values are the
author's decision, and a recommendation beside them would invent a
second author. Its rows are recorded as `authored_in_file`, by the
uploader, from that file (§5.6). A file cannot claim row-level AI
provenance: any such key is ignored, and the dry run says so.

**The existing-mapping diff is kept and shown in both modes** whenever
the product already exists. Today documents mode shows only a count of
affected orgs. The diff gains the two question headers and a link to
each row's expansion. It is "handled, not optimised for", as Jarrod
decided.

## 5. The design

### 5.1 Columns: what stays in front

The decision turns on two answers, so they lead, phrased as questions:

| In front | Why |
|---|---|
| Control | which row this is |
| **Who is responsible?** (classification) | the decision |
| **On whose systems?** (coverage basis; disabled for `customer_owns`) | the decision |
| Recommendation | the recommended answer to each question, and its state in words: *Recommended*, *Held back: reason*, *Accepted in bulk*, *Accepted*, *Chosen by you* |
| Source | where the recommendation came from: *CRM*, *web: host*, *none found* |

**In the expansion, one row at a time:**
- the reasoning, and each cited quote with its source;
- Note, Provider contribution, Customer action, Scope note;
- Objectives and Evidence. They are consequences of the decision, not
  inputs to it, so they belong here. The front row keeps a count ("3 of
  4 objectives · 2 evidence specs"), so a row that credits fewer
  objectives than its control has is still visible without expanding.

**For a held-back row, the expansion opens by default.** Those are the
rows a person reads.

### 5.2 The legend

The legend is built from Heimdal, as the entry says. It answers each
question with the example: scheduled scans on the customer's endpoints
are "On whose systems? The customer's"; SSO into the Heimdal console is
"The vendor's own platform: not coverage". It states plainly that
`assists` is credited exactly like `customer_system` at activation. It
is collapsible, and open by default the first time.

### 5.3 What a recommendation is

For every row, the model returns:
- `classification` and `coverage_basis`;
- reasoning;
- `citations`: each a source id (an uploaded document, or a fetched
  page) and a verbatim quote.

**A citation counts only if the code finds its quote** in that source's
extracted text, after whitespace and case normalisation. A quote that
cannot be found is shown as *unverified* and counts as no source. This
is what makes "cited source" a check in code rather than a model's
claim, the same discipline as `research.py` stamping each page's URL in
code.

Each citation also carries the model's label for what the quoted
passage describes: *the customer's systems*, or *the vendor's own
platform*. H3 below uses it.

### 5.4 The held-back list: enumerated, not thresholded

**Bulk accept works per field, not per row** (a consequence of decision
(a), §3.3):
- **Coverage basis:** a recommended coverage basis is never
  bulk-accepted. Each one is confirmed on its own row, with nothing
  preselected.
- **Classification:** bulk-accepted unless held back below.
- **So a `customer_owns` row can be completed in bulk.** Its coverage
  basis is moot. A row that credits coverage cannot: bulk accept settles
  its classification, and its coverage basis waits for a person.
- **On live's current mappings,** 25 of 103 rows are `customer_owns`, so
  bulk accept would finish about a quarter of rows outright. On the rest
  it removes one of the two decisions.

That is the cost of decision (a), and it is stated here so nobody
expects more. The reasons below still matter for coverage basis. They
are shown on the row as the reason to read it carefully (H3, H5 and H7
are about coverage). They no longer decide whether a coverage basis can
be bulk-accepted, because none can.

A classification is held back for any of these reasons, and the screen
names which. There is no confidence score and none will be added. A
number invites tuning and tells a reviewer nothing about why.

From the entry:
- **H1. Research and the CRM disagree.** Both values are shown; the
  CRM's stands. Includes `merge_research`'s existing disclaim conflicts.
- **H2. No source found:** no citation, or none verified. This covers
  paywalled, missing, self-contradicting, or marketing-only
  documentation.
- **H3. `customer_system` recommended where every verified citation is
  labelled *the vendor's own platform*.**
- **H4. The recommendation changes an existing value** rather than
  filling an empty one.
- **H5. `provider_satisfies` with `customer_system` without a verified
  citation from vendor documentation.** This is the combination that
  activates coverage and writes `provider_satisfies` into the matrix.
  The asymmetry: `platform_only` wrongly recommended loses coverage,
  which is visible and recoverable; this direction claims coverage the
  vendor never provides, in a document an assessor reads.

Proposed additions, each with its reason. Each is Jarrod's to accept
(§9, question b):
- **H6. The row's own text reads as a disclaim** (`baseline_import.
  _disclaims_coverage`, the existing check), while the recommended
  classification credits coverage. The mechanism exists; this only
  routes it.
- **H7. `assists` recommended without a verified citation.** Activation
  credits `assists` exactly like `customer_system`, so it carries H5's
  exposure. And "partial" is precisely where a person's knowledge of the
  deployment matters.
- **H8. The row has a validation problem.** Apply already refuses these.
  Listing them makes the held-back count honest about everything a
  person must touch.

The list lives in one place in code (`HELD_BACK_REASONS`, each with its
sentence). Its test writes each case out (CLAUDE.md, "A test naming a
rule's cases writes them out itself").

### 5.5 Bulk accept

- **"Accept all recommendations"** accepts every classification that is
  not held back, which completes the `customer_owns` rows. It never
  touches a coverage basis. It says how many rows it completed, and how
  many still need a classification or a coverage basis.
- **The rest are decided one at a time:** accept that row's
  recommendation, or choose different values.
- **Apply refuses while any row is undecided.**
- **The server decides what was held back, not the client.** At apply it
  recomputes the held-back set from its stored run (§5.6). A row marked
  bulk-accepted that the run held back gets a 422 naming the row and the
  reason.
- **Nothing is preselected for a held-back row.** That is the near-match
  picker's rule (#118).

### 5.6 Provenance, per row and permanently

**The server holds the recommendation.** A new `baseline_recommendation`
record per run holds:
- product, route, model, who ran it, and when;
- the source documents and fetched pages it read;
- one entry per row: recommended values, reasoning, citations with
  their verified flag, and the held-back reasons.

The run writes nothing else: no product, no version, no control state.

**Apply names the run and says, per row, how it was decided:**
- `bulk_accepted`
- `accepted_individually`
- `chosen_by_person`: differs from the recommendation, or no
  recommendation existed
- `authored_in_file`: the yaml route

**The server checks the claim against the run.** An accepted row must
equal its recommendation. A bulk-accepted row must not have been held
back. The actor is the session's user, never a field in the body.

**Stored on `baseline_control`** (a migration):
- `decided_how` and `decided_by`;
- `decided_at`;
- `recommendation_id` and the row's citations.

Version rows are immutable, so provenance belongs to the version. **A
re-import that writes a new version copies each unchanged row's
provenance forward unchanged.** A bulk-accepted row stays bulk-accepted
in every later version until a person changes it. A no-op re-import
writes nothing, as today.

**Shown:**
- in the wizard;
- on the tool's page (`ToolDetailPanel.tsx`), beside the existing
  "AI-generated baseline mapping -- not vendor-authoritative content"
  warning, which stays;
- in the version history.

Whether the bundle says so too is §9, question c.

### 5.7 Telling a recommendation from a choice

The state is always words (§5.1), with an icon beside it, so colour is
never the only cue. Any new tint gets its WCAG ratio measured and stated
in the slice that adds it (CLAUDE.md).

## 6. What does not change

- **The research rule.** `merge_research` is untouched. Web content may
  propose coverage and may never override a CRM disclaim, in code. H1
  adds a hold. It never adds an override.
- **`assists` is credited like `customer_system`.** The legend says so.
  The wording is not the fix; that gap keeps its own entry.
- **No `control_state.status`, responsibility or SPRS change.** Neither a
  recommendation run, an accept nor an apply touches a control state. A
  test asserts it: control states, history and score are unmoved across
  run, accept and apply. Activation remains the only path from a mapping
  to a tenant, and its rules are §1's.
- **`coverage_basis`'s default never decides a row.** The column defaults
  to `customer_system`. Every path keeps the requirement that a person,
  or an accepted recommendation, sets it.

## 7. Slices

- **First: the event-loop measurement** ("Observed on the way", at the
  end). If generation freezes every request, its fix comes before
  anything here, because it degrades the deployment for everyone at once.
- **A.1. Activation asks once before recording Provider on unset
  responsibilities** (§2's decision).
  - The summary goes in the existing activation dialog, with the same
    refusal when what was shown has changed.
  - Tests: the count and the list match what is written; nothing is
    written on a refusal or with no answer; a confirm writes each
    objective; a status and a hand-set responsibility behave as today.
  - It is independent of the wizard and closes §2's path for mappings
    already on live. So it can land first.
- **T.1. Columns, questions, legend, expansion and the diff in both
  modes.**
  - Frontend only, with no AI change. It is useful on its own: today's
    table, made navigable.
  - Correct the roadmap entry's column list.
- **T.2. Recommendations, displayed.**
  - The `baseline_recommendation` record, and the model's per-row
    recommendation with reasoning and citations.
  - Citation verification in code, and the held-back computation.
  - Shown in the table. Nothing is accepted yet: accepting is still
    choosing the values by hand.
- **T.3. Bulk accept, with provenance on `baseline_control`.**
  - The migration; the apply checks; copying provenance forward on
    re-import.
  - A test per held-back reason; H5 tested on its own.
- **T.4. Provenance shown on the tool's page and in version history,**
  plus whatever §9's question c decides for the bundle.

Each slice is landed in order: bench, PR, CI green, merge, deploy. Each
walk goes in its PR body.

## 8. Verification, mapped to the brief

| Brief | Where |
|---|---|
| `_run_loop` reported first | §1 |
| Column list corrected to ten | §3; the roadmap entry, in T.1 |
| Both modes; the diff still works | §4; T.1's tests and screenshots |
| A held-back row cannot be bulk-accepted, a test per reason | T.3, server-side 422 |
| `provider_satisfies` + `customer_system` with no verified citation held back | H5, tested on its own in T.3 |
| Provenance tells bulk from individual and survives a re-import | §5.6; T.3 |
| Screenshots at 1366×768: collapsed, one row expanded, the legend, the held-back set | T.1 and T.3, in an artifact |
| Contrast measured; colour never the only cue | §5.7, per slice |
| Full suite, ruff, `tsc -b`, vitest, build | every slice |
| No status or SPRS change, asserted | §6's test, T.2 and T.3 |

## 9. Questions for Jarrod before T.2

a. ~~The model recommends coverage basis?~~ **Answered 2026-11-18:
   yes, and never bulk-accepted** (§3.3, §5.4).
b. **H6 to H8:** accept, reject or amend each. H7 now only annotates,
   since no coverage basis is bulk-accepted.
c. **Should the bundle say when a responsibility came from a
   bulk-accepted row?** That is the remaining exposure (§2), where an
   assessor reads it. The CRM already names the contributing products.
   Adding "from a bulk-accepted mapping row, not confirmed for this
   organization" would make the exposure visible rather than smaller.
d. ~~Ask at activation before writing `provider_satisfies` onto an
   unset responsibility?~~ **Answered 2026-11-18: yes, once per
   activation as a summary, in the existing dialog** (§2, slice A.1).
   With (d) in place, (c) is labelling a path that is already closed.
   It is lower priority, and may not be wanted at all.
e. **yaml gets no recommendation** (§4). Agreed?
f. **New, from (d): is `shared` asked too?** The decision names
   `provider_satisfies`. `shared` also asserts an obligation of the MSP,
   though a weaker one, and the existing rule treats it as the
   conservative value when tools disagree. The plan asks only for
   `provider_satisfies` unless told otherwise.
g. **New, from (d): what a path that asks nobody does.** That is
   `start_assessment`'s activations, an API call with no answers, and a
   version move. Today's equivalent for a hand-set responsibility keeps
   the value and records what the tool would have set. **Recommended:**
   the same here. Leave the unset value as it is (the seed default) and
   write the tool's Provider into the history line. The objective is
   then picked up by the next activation that asks, or set by hand.
   The alternative, writing Provider unasked on those paths, would leave
   §2's path open through them.

## 10. Jarrod's walk (after T.3)

Import a tool he knows well from its documents, accept the
recommendations in bulk, and check whether the held-back rows are the
ones he would have stopped at himself. That is the only test of whether
the held-back list is the right list.

## Observed on the way, not in scope

**Generating a baseline freezes the whole app: measured 2026-11-18.**
Moved to its own roadmap entry, "Generating a baseline freezes every
request", and to the front of Â§7. The measurement, on bench
`wingrc_verify_1118e` at `cb1388f`:
- **Setup:** the production app as one uvicorn process, like the
  image's CMD, with the AI provider replaced by a stand-in whose
  `complete()` blocks for 20 s, as the synchronous model call does.
- **Baseline:** `/health` answered in 3 to 4 ms (334 ms on the first
  request after startup).
- **During a generate:** `/health`, sent 2 s in, took **18.1 s**, and
  answered the moment the generate ended (+0.0 s). Every request waits
  out the model call, not just the generating user's.
- **Afterwards:** 3 ms again.
- **Not a factor:** the generate itself returned 500 after its 20.1 s,
  from the stand-in's minimal reply. That happened after the block, so
  it does not bear on the timing. It was not investigated.
