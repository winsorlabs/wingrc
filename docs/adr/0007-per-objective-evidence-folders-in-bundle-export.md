# 7. Per-objective evidence folders in bundle export

Date: 2026-08-06
Status: Accepted — implemented 2026-08-06, see `backend/app/bundle_service.py`
(`_objective_evidence_folder`, `_assign_evidence_path`,
`snapshot_bundle`'s unlinked-evidence query) and `backend/tests/test_bundle.py`
(the "ADR 0007" test group). `docs/roadmap.md`'s bundle-export "Done" entry
is amended to point here.

## Context

The bundle export (`GET /orgs/{org_id}/assessments/{assessment_id}/bundle`,
roadmap item 1, "Done" since the session that shipped it) embeds every
evidence file at a flat `evidence/files/<id-prefix>_<slug>.ext` path. An
assessor working `02_implementation.html` or `evidence/manifest.html`
objective by objective — e.g. AC.L2-3.1.1 objective (a) — has no way to walk
a tree to that objective's evidence; every file lives in one undifferentiated
folder regardless of which control or objective it satisfies.

`Evidence` is many-to-many with objectives via `EvidenceStateLink`
(`models.py:630`, "one artifact can satisfy multiple control objectives via
EvidenceStateLink (evidence minimization)"), so a target folder shape of
`evidence/<family>/<control>/<objective>/` immediately raises three
questions this ADR settles:

1. One evidence file linked to several objectives — does each objective's
   folder get its own copy, or does the folder structure point at one
   canonical file elsewhere?
2. The current filename includes an 8-character id prefix
   (`<id-prefix>_<slug>.ext`) specifically to guarantee uniqueness in the
   flat namespace. Once the folder path itself disambiguates *which*
   objective a file belongs to, is that prefix still doing anything?
3. `Evidence` rows can exist with no `EvidenceStateLink` at all, or with
   every link archived (`is_archived=True`, set by
   `engine.deactivate_org_product` — see that function's docstring: "ALL
   control states with sourced_from_product_id == this product →
   needs_review... Evidence-state links on ALL tool-sourced states →
   archived"). Today, both cases are silently absent from the bundle
   entirely — `snapshot_bundle`'s evidence query only ever joins through
   `ControlState → EvidenceStateLink(is_archived=False) → Evidence`, so
   neither case is fetched, embedded, or listed. A per-objective folder
   structure has nothing to hang either case off of by definition — where,
   if anywhere, should they surface?

The pre-existing code already carries a relevant structural fact: `ev_by_cs`
(built in `snapshot_bundle`) creates one distinct `EvidenceSnap` instance per
`(control_state, evidence)` row, even when the same evidence is linked to
several objectives. Only the *path* on those instances was shared (both
computed by the same pure function of the evidence row alone,
`_ev_zip_rel(ev_id, title, mime)`), not the data model itself. That matters
for option 1 below — it means duplication is a change to what path gets
computed per occurrence, not a restructuring of the snapshot's shape.

## Options considered — evidence linked to multiple objectives

**A. Duplicate the file into every linked objective's folder.** Each
occurrence gets its own path and its own ZIP entry; `artifact_log.txt` gets
one line per path, all sharing the identical SHA-256. An assessor who only
ever opens one objective's folder always finds a complete, independently
verifiable copy there — no need to know the file is "really" filed under a
different objective and cross-reference it. Cost: bundle size scales with
the number of times a file is duplicated across objectives (bounded, since
`EvidenceStateLink` rows per evidence item are small in practice — this is
evidence *reference* fan-out, not fan-out of collection effort).

**B. One canonical copy (e.g. under the first-listed objective, or a
separate `evidence/_shared/`), with every other objective's folder linking
or pointing to it.** Smaller bundle; matches the "capture once, reference
many" evidence-minimization principle this codebase applies everywhere else
(`CLAUDE.md`'s "Minimize evidence" hard rule). But it reintroduces exactly
the navigability problem this ADR exists to fix: an assessor who walks to
`evidence/AC/AC.L2-3.1.1/b/` and finds nothing (or finds a redirect/pointer
file instead of the artifact) has to already know to look elsewhere. A
folder tree that sometimes contains the real file and sometimes contains a
pointer is a worse assessor experience than the current flat structure,
which is at least uniformly flat.

**Decision: A.** The "Minimize evidence" principle governs *collection*
(don't make an MSP re-screenshot the same control twice) and is unaffected
by this choice — nothing about export-time duplication asks anyone to
collect more evidence. It's strictly a bundle-assembly decision, made once,
about how a fixed set of already-collected artifacts gets laid out for
consumption. Assessor navigability at read time outweighs the modest
storage-size cost of duplicating a handful of KB-to-MB files across a
bounded number of objective folders.

## Options considered — filename inside a folder

**A. Keep the `<id-prefix>_<slug>.ext` naming everywhere, unchanged.**
Simplest, and the id guarantees uniqueness unconditionally. But the id
prefix existed specifically to disambiguate files in one flat namespace —
once the folder path carries that disambiguation, a name like
`3f9a21bc_screenshot.png` is dead weight for an assessor trying to identify
the artifact at a glance.

**B. Drop the id prefix unconditionally; use the slugified title alone.**
Reads cleanly, but is unsafe: `Evidence.title` is free text
(`String(400)`, no uniqueness constraint), and two files sharing a title in
the *same* objective folder is a real, not hypothetical, pattern — two
screenshots both left at a tool's default "Screenshot.png", or two exports
both named "export.csv". Unconditional dropping would let one silently
overwrite the other in the ZIP.

**Decision: drop the prefix, but track per-folder name collisions and
append a stable `-2`, `-3`, ... suffix only when a name is actually reused
within one folder** (`_assign_evidence_path`). Filenames read cleanly in
the common case; the id-based disambiguation only reappears, in a milder
numeric form, in the specific case it's still needed. Family/control/
objective path segments themselves need no such treatment — they come from
the reference catalog (`Control.family`, `Control.control_id`,
`AssessmentObjective.objective_key`), never from free user text, so they
carry no collision or sanitization risk the way the title-derived filename
does.

## Options considered — evidence linked to no objective

Two shapes both present as "not linked" but are not the same fact, and
conflating them was flagged as a defect in this ADR's own draft before
implementation:

- **Never linked** — an `Evidence` row with zero `EvidenceStateLink` rows
  into this assessment's control states at all, active or archived. Nothing
  ever put it out of scope; it's just never been attached to anything. This
  is a silent gap in the *current, already-shipped* bundle exporter,
  independent of this ADR's folder restructuring — the bytes are real, sit
  in storage, and simply never get read for export today.
- **All links archived** — at least one `EvidenceStateLink` into this
  assessment exists, but every one has `is_archived=True`. This is not an
  accident; it's the direct, provenance-tracked, and *reversible* output of
  `engine.deactivate_org_product` — checked directly against that function
  and `activate_org_product`'s reactivation path (`archived_by_product` FK,
  restored verbatim on reactivation, per-link audit rows
  `evidence_state_link.archive`/`.restore`). Evidence in this state was
  deliberately taken out of the assessment's current scope by an MSP action
  that can be undone, not deleted.

**A. Treat both as "unlinked" and list them together.** Simplest query (one
`NOT EXISTS` against active links only), but wrong: it would resurface, in
a document assembled specifically for a C3PAO assessor, evidence the
customer's MSP deliberately retired from scope. That's worse than today's
silence — today's silence is at least neutral; a misleading "here's some
evidence" listing for retired coverage would actively misrepresent current
scope to the party whose job is to verify it.

**B. Exclude both — leave the never-linked gap exactly as it is today.**
Closes nothing; the silent-gap case is a real defect (bytes exist, nothing
in the export says so) that this restructuring work was already touching
the relevant query to fix.

**Decision: include never-linked evidence only; exclude archived-link
evidence entirely, unconditionally.** `snapshot_bundle`'s unlinked query
excludes an `Evidence` row if *any* `EvidenceStateLink` exists into this
assessment's control states, regardless of `is_archived` — so the mere
existence of an archived link (not just an active one) is enough to keep
that evidence out of the "Unlinked Evidence" manifest section. Never-linked
evidence is embedded under `evidence/unlinked/` and listed in a dedicated
manifest section, with its header text stating explicitly that
retired-via-deactivation evidence is deliberately excluded and pointing to
Outstanding Items (where the resulting `needs_review` controls already
surface) — so the omission reads as a documented decision, not a missed
case, to anyone reading the bundle later.

## Consequences

- `BundleSnapshot.evidence_files`/`evidence_hashes` are now keyed by ZIP
  path, not `evidence_id` — one `evidence_id` can own several paths. File
  bytes are still fetched from storage exactly once per `evidence_id`
  (unchanged dedup behavior) and then fanned out to every path it earns.
- `BundleSnapshot` gained `unlinked_evidence: list[EvidenceSnap]`.
- `02_implementation.html` and `evidence/manifest.html` needed no rendering
  changes beyond what falls out of the path change — both already rendered
  one `EvidenceSnap` occurrence per (objective, evidence) pairing; they just
  now receive a different, objective-specific path on each occurrence
  instead of a shared canonical one.
- Two existing tests encoded the old flat `evidence/files/...` shape as a
  literal assumption (`test_bundle_embeds_evidence_file`,
  `test_bundle_manifest_links_resolve`) and were updated to check the
  underlying invariant (a real file is embedded; every manifest href
  resolves to a real ZIP entry) without assuming a fixed-depth path.
- Four new tests cover this ADR directly: duplication (same evidence, two
  objectives → two paths, identical hash), never-linked evidence appearing
  under `evidence/unlinked/` and in the manifest, archived-link evidence
  being excluded end-to-end (not embedded anywhere, not in the manifest),
  and the intra-folder filename collision suffix.
- Not part of the I.1–I.9 auth/RBAC slice numbering — this amends the
  already-"Done" bundle-export roadmap item. `docs/roadmap.md`'s entry for
  it now points here rather than reading as untouched since that session.
