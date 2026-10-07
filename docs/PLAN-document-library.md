# Plan — Document Library (roadmap item N)

**Status:** N.1 ✅ DONE (2026-09-24, migration 0059 — the document/version
split, the status transition table, the republish decision). **N.2 ✅ DONE**
(2026-09-28, no migration — GFM-subset Markdown as the body format, TipTap
on a constrained schema, structured server-side diffs, versions-plus-
audit_log history, approved documents rendered into the bundle and PDF).
**N.3 ✅ DONE** (2026-09-30, migration 0062 — `document_approval` as the
single authoritative approval record, a derived review cadence that is
never stored, and a digest job that changes no document state). See
`docs/roadmap.md`'s Done entries for all three writeups, including N.2's §0
format decision and its cost, the post-commit RLS audit N.2 section 8 asked
for, and N.3's §0 record design. N.4–N.5 not started. This document is the
sequencing spec and the cross-cutting rules; each slice gets its own prompt
when it starts. **N.5 or N.4 next** — N.5 is independent of everything
shipped so far; N.4 wants N.1's versioning exercised for a while first and
is the larger of the two.

**Reconciles two prior specs, both superseded by this one:** `ROADMAP.md`'s
item F ("Template document library") and `docs/roadmap.md`'s original item
N — flagged 2026-09-07 as likely-duplicated, never reconciled until now.
This document is the single current spec; both of those sections now point
here instead of carrying their own inline design.

Jarrod's asks, beyond roadmap N's original scope:

1. MSP/master-org template sets with replaceable variables and branding.
2. Edit in browser, with version control and audit logging.
3. Approval per document, plus a per-document review cadence (default
   annual, adjustable to quarterly/monthly/etc.).
4. Suggested documentation driven by environment type and tools in use.

Two decisions he made up front:

- **Documents convert to structured text on import** (.docx → Markdown/
  HTML), edited in browser, rendered back out with branding at export.
  Accepted cost: complex Word formatting is lost and his existing set gets
  re-styled once inside WinGRC.
- **Templates are versioned, adoption is explicit.** A client copy records
  the template version it came from; a master edit creates a new version
  and client orgs adopt it deliberately.

---

## Grounding — verified in the code, not assumed

- **`Document` does not exist.** No model, no `routers/documents.py`. N is
  genuinely unstarted. (`importers/document.py` is the vendor-CRM baseline
  extractor — a different feature entirely. The roadmap already warns about
  this confusion; don't repeat it.)
- **`Organization.logo_storage_key` exists** (migration 0011). Branding has
  a source.
- **Substitution values largely exist already**: `Organization` carries
  name, address, city, state, postal, country, phone, website, CAGE, UEI,
  industry, year established. `Contact` + `ContactDocumentationRole` give
  role-holders (`security_officer`, `it_admin`, and the rest of migration
  0013's vocabulary).
- **`SystemDescription.system_type` is already taken, and means something
  else** — its CHECK constrains it to `major_application |
  general_support_system | minor_application`, the NIST/FedRAMP sense. What
  Jarrod means by "system type" for document suggestions (AD vs Entra-only,
  on-prem vs cloud) is a different axis and **must not reuse that column or
  that name.** Call it environment profile or platform profile.
- **`OrgProduct`** is the existing record of which tools a tenant has
  active — the other half of the suggestion input, already there.
- **Monetization boundary, from the roadmap**: the matching engine,
  tagging, and publish/approve flow are core and open-source. Curated
  template *content* is a separately distributed seed script, marked by
  `is_template_derived` / `template_ref`, with no code-level paywall.
  Jarrod's own MSP document set uses the same mechanism as any other
  operator's; it is not a special case.

---

## Cross-cutting rules — these apply to every slice

**1. Version from day one. Do not retrofit it.**

Roadmap item P is the direct precedent: baseline mappings were built
mutable, approved tenants inherited silent rewrites, and versioning had to
be added later across five tables and two migrations. Documents are the
same class of artifact — approved records an assessor reads — and will hit
the same wall harder, because a policy document *is* the evidence rather
than pointing at it.

`document_version` is append-only from the first migration. Editing creates
a version; it never mutates one. This is not a later slice.

**2. Substitute variables at import, not at render.**

When a template becomes a client document, resolve `{Company Name}` and the
rest **once**, into the stored text, and record which values were used. Do
not leave placeholders to be interpolated at render time.

Reason: an approved document must not change because someone later edited
the org profile. Render-time substitution means the approved artifact and
the rendered artifact can differ, silently, which is the exact failure
mode this codebase refuses everywhere else. Same for branding — the logo in
force at import is part of what was approved.

**3. An unresolved variable blocks; it never renders raw.**

A policy reading "{Company Name} shall maintain..." in front of a C3PAO is
a serious failure. Import reports every variable it could not resolve and
refuses to produce a publishable document until they are filled — by the
org profile, or by the operator explicitly.

**4. Candidates, never auto-applied.** Suggested documents are suggestions:
nothing is added, removed or approved automatically. Publishing attaches
evidence; it never marks a control met. Roadmap N is already explicit —
"`control_state.status` is NOT changed — evidence is attached; engineer
must review and manually mark objectives met."

**5. Approval requires an authenticated session.** No one-click approval
links in email — the same hard constraint D.3 and the review cycles hold.
Email notifies; the app authenticates and records the approver.

**6. Reuse the review-cycle machinery.** `review_cycles.py` already solved
notification routing, delivery tracking (`notified_at` /
`notification_error`), the not-notified-vs-no-response distinction, and
authenticated sign-off. Document approvals are the same shape. Reuse it or
explain why it doesn't fit — do not build a parallel notifier.

**7. `c3pao_assessor` stays read-only** across all of this.

---

## The slices

### N.1 — Core library, versioned from day one

`document` + `document_version` (append-only) + `document_objective_tag`.
CRUD, RLS, roles. Publish → `Evidence` (`kind='reference'`) +
`EvidenceStateLink` against the active assessment's `control_state` rows
for each tagged objective, status untouched.

Editing can be a plain text area in this slice — the editor is N.2. The
point of N.1 is that the record shape is right.

Roadmap N's field list stands: `doc_id` (stable human-readable, "AC-POL-001",
unique per org), `title`, `doc_type`, `status`, `body`, `storage_key`
(nullable, for file-backed documents), `is_template_derived`,
`template_ref`, `approved_at`, `approved_by_contact_id`.

**Ships first and alone.** Everything else builds on this shape.

### N.2 — Browser editing, diffs, audit ✅ DONE (2026-09-28)

Rich-text editing against the versioned store. Version history with a
readable diff between any two versions. Audit log entries for create,
edit, publish, supersede.

Shipped as specified, with the open questions settled — see
`docs/roadmap.md`'s Done entry for the full writeup. In short: the body
format is **GFM-subset Markdown**, decided not on diff quality but on the
fact that a body renders in two places (the app and the WeasyPrint PDF)
and Markdown lets both renderers construct their own output rather than
forwarding operator markup; the editor is **TipTap 3** on a schema
constrained to exactly the serializable subset, asserted by a test rather
than assumed; concurrent editing is **optimistic concurrency, not a
lock**; there is **no autosave**, because append-only versions make one
either absurd or illegal. The costs — no merged table cells, no inline
images until N.4 — were confirmed with Jarrod before the slice started.

Two things below are now settled by that slice rather than open: the
"editing can be a plain text area" note under N.1 is superseded, and
N.3's dependency on N.2 is satisfied.

### N.3 — Approval and review cadence ✅ DONE (2026-09-30)

Approval per document version — a version is approved, not a document, or
editing after approval silently changes an approved artifact.

Per-document `review_cadence_months`, defaulting to 12, adjustable. A
scheduled job surfaces documents due for re-approval and notifies. Note
`Organization.review_cadence_months` already exists for the users/devices
review cycle — this is a *separate, per-document* cadence, and the two must
not be conflated in the schema or the UI.

Re-approval of an unchanged document is a real event with its own record:
"reviewed and still current on <date> by <person>" is exactly what a
periodic-review control wants.

**As built.** `document_approval` (append-only, one row per decision,
`approval_type` initial/reaffirmation) is the single authoritative record;
`DocumentVersion.approved_at`/`approved_by_contact_id` are now a documented
denormalized snapshot of the *initial* approval only, pinned by a test, and
never read to answer a cadence question. Overdue is **derived** on every
read from the newest approval plus `cadence_months` — there is no
`next_due_at` column, deliberately, because it would go stale the moment an
operator changed the cadence.

Overdue **flags, never invalidates**: evidence links stay unarchived,
`control_state` is untouched, the SPRS score does not move. The scheduled
digest (`scheduler.py:_document_review_digest`) notifies each due
document's named approver plus every `security_officer`/`it_admin`, one
email per recipient per org naming nothing, and writes only
`document_review_notification` rows.

Cross-cutting rule 6 was honoured by reusing the review-cycle *machinery*
(`email_service`, the digest shape, the `notified_at`-set-once discipline)
but **not** its tables: `ReviewCycleReviewer` is `cycle_id`-scoped to the
users/devices attestation cycle and its reviewers are Users, while document
approvers are Contacts who often have no login. D.3 hit the same wall and
answered it the same way with `LiongardSyncNotification`.

Still open, raised rather than settled: whether overdue review state
belongs in the SSP narrative or the exported bundle.

### N.4 — MSP template library

Master-org templates with variables and branding, imported into client orgs
per the two decisions above: substitution at import, versioned templates
with explicit adoption.

Variable vocabulary resolves from `Organization`, `Contact` +
`ContactDocumentationRole`, and `SystemDescription`. Define it as a
documented, extensible registry rather than scattered string replacement.
Unknown variables are reported, never silently left in place.

"Which clients are on which template version" must be answerable from the
UI — that is the question this design exists to answer, and the same one
`product_deployment_footprint()` answers for baselines.

**Jarrod's existing document set gets generalized as part of this slice** —
turning his real documents into templates is the acceptance test.

**Two inputs that arrived before this slice did (Lists slice, 2026-10-07)
— design for them, don't rediscover them:**

- **`[PLACEHOLDER - reason]` is already a convention in Jarrod's real
  data.** His Authorized-Entities workbook marks missing values as
  `[PLACEHOLDER - not in Datto RMM export]`,
  `[PLACEHOLDER - BIOS FW version not collected]`: a value *deliberately
  absent, with the reason recorded*. The Lists slice made it first-class
  (`domain.placeholder_reason()`, stored verbatim, rendered distinctly).
  N.4's `{Company Name}` is the other half — a value *to be filled*. The
  variable registry should treat both: an unresolved `{Variable}` is
  reported (as above), and a `[PLACEHOLDER - reason]` is preserved as a
  stated gap, not flagged as an error and not stripped. One syntax family,
  not two unrelated schemes.
- **Documents reference lists.** His `Windows 11 Workstation Baseline`
  says *"Authorized devices are recorded in New Lists/AC/3.1.1c Authorized
  Devices"* and names other lists the same way. A list is a live view
  (`catalog.py` `ListView.id`, e.g. `3.1.1c-authorized-devices`), not a
  document, so a reference should resolve to the view — and in the bundle,
  to that list as exported at the same point in time — rather than to a
  path in someone's file share. Not built; a requirement for N.4 to design.

### N.5 — Suggested documentation

A new environment/platform profile per org (identity: AD / Entra / hybrid;
hosting; whatever else earns its place), plus the tools already recorded in
`OrgProduct`, driving a suggestion list: "an AD baseline is not suggested
for an Entra-only environment."

Suggestions only. Never auto-add, never auto-remove, never hide a document
someone already adopted because the profile changed. Show why each document
is suggested — "suggested because: Entra ID, RocketCyber active" — so the
operator can judge the rule rather than trust it.

---

## Sequencing and what to start

```
N.1 (core, versioned)
 ├── N.2 (editing, diffs)
 ├── N.3 (approval, cadence)       ← needs N.1; better after N.2
 ├── N.4 (MSP templates)           ← needs N.1's versioning
 └── N.5 (suggestions)             ← needs N.1; independent of N.2–N.4
```

N.1 first, alone; N.2 landed next. N.3's precondition (approval attaching
to something a human can read and diff) is now met, and N.5 remains
independent of both.
N.4 is the biggest and wants N.1's versioning settled and exercised.

**Start with N.1.** Its prompt is separate.

---

## Open questions to settle as they arrive, not now

- **.docx → Markdown conversion library.** `mammoth` (docx→HTML) and
  `pandoc` are the candidates; neither is currently a dependency. Decide in
  N.4 against Jarrod's real documents, not in the abstract — the right
  answer depends on what his set actually contains.
- **Export format.** WeasyPrint is already in the stack for PDF. Whether
  documents also export as .docx is a separate question; don't assume.
- **OCR** remains the recorded gap it has been since the ingestion slice.
