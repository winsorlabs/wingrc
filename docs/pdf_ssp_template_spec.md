# PDF SSP Template — Design Spec

Scoped in advance, for implementation whenever this slice comes up. Not built yet.

## Scope

One consolidated PDF combining:
- System description
- Implementation statements — the `[a]`/`[b]`/`[c]` per-objective statements grouped under each control, exactly as `02_implementation.html` already does it
- Personnel / responsibility per control

Out of scope for this pass: cover page, evidence manifest, and the scoring/summary sections stay as separate HTML in the bundle. Can revisit combining everything into one document later if it turns out to matter.

## Style

No strong preference was expressed, so recommending: formal/official compliance-document look, not a marketing deliverable. Serif or clean sans body text, minimal color, generous white space. Org logo used modestly — letterhead-style at the top of the first page, not full-bleed branding.

## Structure

- Auto-generated Table of Contents, organized by CMMC family → control, linking to page numbers.
- Running header: org name + assessment name.
- Running footer: page X of Y + generation timestamp.
- No auto-stamped CUI/distribution marking. That determination belongs to the OSA, not something WinGRC should assert on their behalf — leave room for the customer to add their own if they need to, don't presume to fill it in.

## Content assembly

Reuse the existing data assembly logic that already powers `02_implementation.html`, `01_system_description.html`, and `03_personnel.html` — that logic (especially the `[a]`/`[b]`/`[c]` grouping) is already correct and verified. This is a re-skin for pagination, not a rewrite. Don't let this turn into a parallel content-assembly path that can drift from the one already tested.

## Technical approach

Recommend **WeasyPrint** over a headless-Chromium approach (e.g. Playwright print-to-PDF):
- Purpose-built for paged/print CSS — page counters, running headers/footers, and TOC generation via `@page`, `string-set`, `target-counter()` are native to it, not bolted on.
- No full browser binary to bundle — needs Pango/cairo/gdk-pixbuf system libraries in the Docker image, which is a smaller, more auditable footprint than shipping Chromium. Relevant given the FIPS deployment profile is still on the roadmap — every added binary dependency is scrutiny surface later.

Try single-pass CSS Paged Media TOC generation first. Fall back to a two-pass render (render once to learn page numbers, then render the TOC) only if WeasyPrint's TOC support proves inadequate for the family → control → objective nesting depth.

## Bundle integration

- New file: `ssp/system_security_plan.pdf`, added **alongside** the existing three HTML files — don't replace them. They're already tested, verified, lightweight, and greppable; the PDF is the polished deliverable, the HTML is the fallback/reference.
- Must flow through the existing artifact-hashing mechanism — the new PDF needs its own `SHA-256 | hash | path` entry in `artifact_log.txt`, same as every other embedded file or generated page. Easy to forget when adding a new render path; flag this explicitly to whoever implements it.
- Expect bundle generation to take measurably longer once this ships (real HTML→PDF rendering of a potentially long document). The existing "Generating…" disabled-button UI state already handles this gracefully — no further frontend change needed for this reason alone.

## Explicitly deferred

- Consolidating cover page, evidence manifest, and summary/scoring into the same PDF.
- Auto-populated CUI/distribution markings.

## Addendum: Network Diagram & Data Flow Diagram

Added scope, sourced from a separate side project that surveys the customer/MSP and generates both diagrams from that data. Not built yet — this expands the original template-only scope to include a data-model and frontend addition.

### Format
- **SVG primary.** WeasyPrint embeds SVG at true vector quality — network diagrams carry small labels (device names, IP ranges, zone boundaries) that need to stay legible under zoom, which raster degrades. SVG also renders natively in-browser, so one file serves both the in-app preview and the PDF embed.
- **PNG accepted as fallback** for any diagram the survey tool can't cleanly export as SVG.
- Sanitize SVG uploads on ingest (strip `<script>` and external references) before treating as trusted vector data — SVG is XML and can carry scripting content.

### Data model / frontend
- Two dedicated attachment slots on **System Description** (not generic anonymous evidence) — "Network Diagram" and "Data Flow Diagram" — pinned so they always surface in the SSP regardless of objective tagging, similar to how the org logo has a dedicated slot today.
- Reuse the existing evidence pipeline underneath: MinIO storage, MIME allowlist extended to `image/svg+xml` and `image/png`, magic-byte check, existing SHA-256 hashing.
- Same evidence item can still optionally link to relevant objectives (CM.L2-3.4.1 system component inventory, SC.L2-3.13.x boundary protection are likely candidates) via the existing many-to-many evidence-to-objective linking — no new linking mechanism needed.
- UI: two upload widgets in the System Description editor, each with a live thumbnail preview (SVG/PNG render natively in-browser) and a replace action. Replacement follows existing evidence conventions — retain prior version, don't overwrite/lose history.

### PDF placement
Both images embedded directly in the System Description section of the SSP PDF, near the CUI flow narrative, full-width, each with clear surrounding space rather than cramped inline with text.

### Open risk
Hard interchange contract between two separate projects (WinGRC and the survey/diagram tool). Test the survey tool's actual real output against WinGRC's real ingest path before considering this done — format mismatches or unsupported SVG features would surface as broken/blank images in a document meant for a C3PAO.

## Addendum 2: Gap analysis against NIST's official CUI SSP template

Reviewed all 20 pages of `cui-ssp-template-final.docx` (NIST SP 800-171 r2 upd1). Structure: (1) System Identification, (2) System Environment — includes the topology/network diagram requirement, confirming the diagram addendum above is correctly placed — (3) Requirements (all 14 families, matches existing implementation statement coverage), (4) Record of Changes.

### Real feature gap — needs its own discussion, not just a PDF template addition
- **Component/asset inventory.** Template section 2.1/2.2 requires "a complete and accurate listing of all hardware and software components, including make/OEM, model, version, and person/role responsible." Maps conceptually to CM.L2-3.4.1, currently tracked only as a pass/fail control status. No structured inventory entity currently exists in the app. Open question: build a real Hardware/Software Inventory feature, or keep treating this as an uploaded evidence artifact (spreadsheet) referenced from the SSP? Different scope depending on the answer — decide before building either.

### Document-structure gap
- **Record of Changes (template section 4).** Simple Date / Description / Made By table tracking SSP revisions over time. Distinct from the existing audit_log (system-action log, not human-readable version history). Decide: derive automatically from prior bundle-export dates, or add a lightweight manually-curated changelog field.

### Small additions
- System Unique Identifier (1.1.2) — a formal identifier distinct from internal org/assessment IDs.
- Standard FIPS 199 categorization statement ("Moderate Impact for Confidentiality") — boilerplate, essentially constant for CUI systems; can likely be fixed text rather than a user-entered field.
- User/role headcount table (1.3.1) — number of end users vs. privileged/admin users.
- Hardware/software ownership and maintenance statement (2.3) — yes/no + explanation.

### Style note (supersedes earlier "formal/official" recommendation)
Content completeness should track the NIST template closely — that's what a C3PAO expects to find. Visual execution should not — lean toward the "clean modern report" direction using org branding, not the bare government-form look of the reference template.
