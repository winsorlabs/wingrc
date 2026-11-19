PLAN -- the Tool Import wizard: each row a decision a person can make.

**Status: plan only, nothing built (2026-11-18).** Jarrod reads the shape
before any slice starts. The spec is `docs/roadmap.md`'s entry "The Tool
Import wizard: each row a decision a person can make": Jarrod's request,
his four decisions, the Heimdal legend and the vocabulary. This plan does
not restate them. It reports what changed since the entry was written
(2026-11-02) and designs what the entry leaves open. Code was read at
`8381328`. Live counts were read on 2026-11-18 and are counts only.

**T.1 built (2026-11-19, branch `tool-import-t1`); T.2 onward not
started.** Jarrod answered §9 the same day (recorded there).

**Before T.1: still nothing built (2026-11-19).** #122 merged this plan and no code.
The wizard on live is the one §0 describes. Jarrod expected otherwise, so
the PR that amends this plan says in its first line that it is a plan.

## 0. Jarrod's reaction to today's screen (2026-11-19)

He used the current wizard and reacted. This section is the brief's
reading of his screenshot and his words, checked against
`ToolImportWizard.tsx` at `6789aa0`. It is folded into §5 and §7 before
T.2, not left as polish after it. **His test of the result: he can look
at a row and know what is being asked of him.** §8 adds it as a check.

What he said, in his words:
- **"The Product Headers labels are dull and look messy."** The top
  fieldset's labels are the field names themselves: `key`, `name`,
  `provider`, `category`, `asset_type`, `framework`, `role`,
  `assumed_config (comma-separated)`. They are database column names on
  a screen a person uses (§5.0).
- **"Note, Provider Contribution, Customer Action and Scope notes are
  unreadable because they are so small you cannot see hardly any text
  without slowly scrolling through."** Each is a one-line input in a
  ten-column table and shows about six characters ("Custo", "Fenix",
  "Applie"). §5.1 already moves these four into the row's expansion. His
  reaction is the reason, measured on his own screen.
- **"I still do not fully understand what the use of each column is.
  There should be a legend at the top that tells you what you should be
  considering for each column."** That is the legend of §5.2, which he
  asked for on 2026-11-02 and again now. It becomes T.1's first
  deliverable (§7), not one item among five.

Two more, from the screenshot, which he did not name but which take most
of it (the brief, 2026-11-19):
- **The validation message is repeated in full on every invalid row.**
  Under each row's coverage basis the screen prints
  "controls[N].coverage_basis must be explicitly set to one of
  ['assists', 'customer_system', 'platform_only']"
  (`baseline_import.py`), and under a disclaimed row's classification the
  disclaim warning, several lines long. Together they take more height
  than the row data. §5.8 replaces them with one banner and a marker per
  row.
- **Nearly every row is tinted invalid, so the tint says nothing.** In
  documents mode that is the expected state, not a defect. The model
  never fills coverage basis, by design (`baseline.py`, §3), so every row
  that is not `customer_owns` arrives without one, flagged. A question
  nobody has answered yet is shown as an error. When everything is
  flagged, nothing is (§5.8).

Found while checking, not in the screenshot:
- **`assumed_config` is split on commas when the field loses focus**
  (`commitProductListField`). Its items are sentences, and real ones hold
  commas: RocketCyber's "Detection sources: Windows Event Log,
  M365/Entra (SaaS Alerts), FortiGate syslog, …" is one item. Click in
  and in out, and it becomes five on the next Apply. The label tells the
  person to use commas. §5.0 makes it one item per line.
  - **Live needs no repair (read 2026-11-19, Jarrod's question).** Live
    holds 4 products and 20 items. Every product still has at least one
    item containing a comma, and the split runs on every comma, so none
    was ever committed through that field. No item starts lowercase or is
    empty. RocketCyber's list is identical to `baselines/rocketcyber.yaml`.
    Read as JSON in a read-only transaction, not by eye.

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

### 5.0 The product's own fields, in words

The fieldset is titled for what it holds, "About this tool", and each
field is labelled for what a person decides there, with a one-line hint.
Labels are the plan's proposal; the wording is settled on the bench
screenshot.

| Field | Label | Hint |
|---|---|---|
| `name` | Tool name | as it appears in the tools library |
| `provider` | Vendor | |
| `key` | Short ID | used in file names; cannot change once the tool exists (already disabled when editing) |
| `role` | What it does here | one or two sentences, shown on the tool's page |
| `category` | Provider type | ESP, CSP or SPA, as the CMMC scoping guidance uses them |
| `asset_type` | Asset type | how this tool's assets are categorised in scope, e.g. SPA |
| `framework` | Framework | read-only, there is one |
| `assumed_config` | Assumed configuration | one item per line: what this mapping assumes is configured. Each line is shown on the tool's page |

- **Name and vendor lead.** Short ID moves after them, since a person
  reads a name and the ID follows from it.
- **`assumed_config` becomes a multi-line field, one item per line,**
  which ends the comma split in §0. A test feeds an item holding a comma
  and asserts it survives a focus and a blur unchanged.
- **Framework stops being free text** where only one exists. What
  `framework` must equal is checked in the code first. **Checked
  (2026-11-19): nothing reads it.** It is carried through the YAML, but
  apply seeds every product against `nist-800-171-r2`
  (`seeds/baselines.py`, `_FRAMEWORK_KEY`), and Edit mode already sent it
  blank. T.1 shows it read-only.
- **The category hint above was wrong, and T.1 does not use it.** By
  32 CFR 170.4 and 170.19 (read 2026-11-19), SPA, security protection
  assets, is an asset category (Table 3), not a provider type. CSP is a
  kind of ESP (Table 4: "whether the ESP is a Cloud Service Provider"),
  not a separate type. So `category` is labelled "Provider type", hinted
  ESP and saying when it is also a CSP, and `asset_type` is labelled "Asset
  category", hinted SPA. Live's four products are all ESP and SPA.
- The category and asset type vocabulary is checked against the
  scoping guidance before the hints are written, not taken from
  `baseline.py`'s comment.

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

**At the top of the table, before the first row, and the first thing
T.1 builds** (§0). It has one line per column a person sees, in the
order they appear, each saying what to consider there, not defining
the value:
- **Control:** which requirement this row is about.
- **Who is responsible?** Does the tool do this for the customer, do
  they share it, or does the customer still own it whatever the tool
  does? A vendor CRM listing a control is not an answer: most controls a
  product touches the customer still owns (CLAUDE.md's hard rule).
- **On whose systems?** Where the tool's part happens. Heimdal's
  scheduled scans run on the customer's endpoints: the customer's. SSO
  into the Heimdal console protects only the vendor's platform: not
  coverage, and never credited. `assists` is credited exactly like
  `customer_system` at activation, and the legend says so.
- **Recommendation and Source:** what the research suggests and where it
  read it (T.2 onward). Before T.2 these columns are absent, and so are
  their lines.
- **In the row's expansion:** what the vendor does, what the customer
  must do, any scope limit, and a note. These are written for the
  assessor's reader; the expansion says so.

The Heimdal example is the legend's worked example, as the entry says.
It is collapsible, open by default until the person closes it, and
remembered per browser. Each column header carries the same sentence as
its tooltip, so the question is answered where the eye is.

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

### 5.8 Problems: one banner, a marker per row

From §0. Today each invalid row prints its full validation message, and
nearly every row is invalid.
- **One banner above the table** gives the counts in words, each a
  filter: "12 rows need *On whose systems?* answered · 2 rows have a
  control the CRM disclaims · 1 row has an unknown control". The
  backend's `controls[N].field` messages never reach the screen as text.
- **Each affected row carries a short marker** in the front row, naming
  what it needs ("Answer: on whose systems?"), not the rule.
- **The detail goes in the row's expansion,** which opens for a row with
  a problem, as §5.1 already does for a held-back row.
- **An unanswered question is not an error.** A coverage basis the model
  left blank by design is shown as *to decide*, untinted. The tint is
  kept for what is wrong: a control that does not exist, an objective not
  in that control, a disclaimed control. A screen where most rows are
  to-decide shows that as a count in the banner, not as a red table.
- **Apply stays refused** while anything is undecided or wrong, as now.
  The banner says what stands between the person and Apply.
- A disclaim flag stays advisory, as now (`disclaimFlag`), and is never
  counted as blocking.

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
  It did, and the fix was built first (2026-11-19, the roadmap entry
  "Generating a baseline froze every request").
- **A.1. Activation asks once before recording Provider on unset
  responsibilities** (§2's decision).
  - The summary goes in the existing activation dialog, with the same
    refusal when what was shown has changed.
  - Tests: the count and the list match what is written; nothing is
    written on a refusal or with no answer; a confirm writes each
    objective; a status and a hand-set responsibility behave as today.
  - It is independent of the wizard and closes §2's path for mappings
    already on live. So it can land first.
- **T.1. Legend first, then the product's fields, columns, questions,
  expansion, problems and the diff in both modes** (§0).
  - In order within the slice: the legend (§5.2); the product's fields
    in words (§5.0); the four text columns into the expansion (§5.1);
    one problems banner and per-row markers (§5.8); the diff.
  - Frontend only, with no AI change, except that the banner's counts
    may need the dry run to return its problems keyed by field rather
    than as sentences. It is useful on its own: today's table, made
    navigable.
  - Correct the roadmap entry's column list. (Already done 2026-11-18.)
  - **Built 2026-11-19.** What differs from the proposal above:
    - **The banner's counts needed the dry run's help, as foreseen.** Each
      `ValidationProblem` now carries a `kind` from
      `baseline_import.PROBLEM_KINDS` and the `value` it is about.
      `tests/test_baseline_problem_kinds.py` parses the module and fails
      on a problem without a listed kind. The screen's sentences are in
      `frontend/src/lib/baselineReview.ts`, whose test writes out the
      backend's sixteen kinds.
    - **"3 of 4 objectives" needed each control's objective keys,** so
      the dry run returns `control_objectives`.
    - **The Source column stays** with today's meaning, the row's origin
      (document or web research), since it exists now. Its legend line
      says so. T.2's recommendation source is a different thing.
    - **The legend's Recommendation line is absent**, as planned before
      T.2.
    - **The diff in documents mode is collapsed** under "Changes against
      version N", above the banner. Each control links to its row.
    - The screenshot at Jarrod's own screen size waits for him to say
      what it is; the bench shots are 1366×768 and 1920×1080.
  - The bench screenshot is at Jarrod's screen size as well as
    1366×768, with a real document's generated rows, not a fixture.
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
| Jarrod's test: he can look at a row and know what is being asked of him (§0) | T.1's walk, his, on a real document's rows |
| No validation sentence repeated per row; the tint marks only what is wrong (§5.8) | T.1's screenshot and a component test |
| An `assumed_config` item holding a comma survives focus and blur (§5.0) | T.1, a component test |
| Contrast measured; colour never the only cue | §5.7, per slice |
| Full suite, ruff, `tsc -b`, vitest, build | every slice |
| No status or SPRS change, asserted | §6's test, T.2 and T.3 |

## 9. Questions for Jarrod before T.2

a. ~~The model recommends coverage basis?~~ **Answered 2026-11-18:
   yes, and never bulk-accepted** (§3.3, §5.4).
b. ~~**H6 to H8:** accept, reject or amend each.~~ **Answered
   2026-11-19: all three accepted as written.**
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

   **Answered 2026-11-19 (Jarrod): no.** With (d), activation asks
   before writing `provider_satisfies` onto an unset responsibility, so the
   path is closed. Labelling a closed path in an assessor-facing document
   adds noise without adding safety. **Do not reopen this** unless a path
   that writes responsibility unasked comes back.
e. **yaml gets no recommendation** (§4). **Agreed (Jarrod,
   2026-11-19):** a hand-authored file carries its author's values, and
   the recommendation column does not appear in yaml mode at all.
f. **New, from (d): is `shared` asked too?** The decision names
   `provider_satisfies`. `shared` also asserts an obligation of the MSP,
   though a weaker one, and the existing rule treats it as the
   conservative value when tools disagree. The plan asks only for
   `provider_satisfies` unless told otherwise.

   **Answered 2026-11-19 (Jarrod): yes, ask for `shared` too.** It still
   asserts the MSP carries part of the obligation, and it prints in the
   customer responsibility matrix the same way. The existing rule makes it
   the conservative value when tools disagree, so it is written often. The
   cost is one more line in a dialog already shown.
g. **New, from (d): what a path that asks nobody does.** That is
   `start_assessment`'s activations, an API call with no answers, and a
   version move. Today's equivalent for a hand-set responsibility keeps
   the value and records what the tool would have set. **Recommended:**
   the same here. Leave the unset value as it is (the seed default) and
   write the tool's Provider into the history line. The objective is
   then picked up by the next activation that asks, or set by hand.
   The alternative, writing Provider unasked on those paths, would leave
   §2's path open through them.

   **Answered 2026-11-19 (Jarrod): the recommendation.** On every path
   that asks nobody, leave the unset value at the seed default and write
   what the tool would have set into the history line. That is how a
   hand-set responsibility is already treated there, so it stays one rule.

## 10. Jarrod's walk (after T.3)

Import a tool he knows well from its documents, accept the
recommendations in bulk, and check whether the held-back rows are the
ones he would have stopped at himself. That is the only test of whether
the held-back list is the right list.

## Observed on the way, not in scope

**Generating a baseline freezes the whole app: measured 2026-11-18.**
Moved to its own roadmap entry, "Generating a baseline froze every
request" (fixed 2026-11-19), and to the front of §7. The measurement, on bench
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
