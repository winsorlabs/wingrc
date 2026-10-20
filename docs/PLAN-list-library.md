PLAN — the list library (roadmap L).

Written against Jarrod's real `CMMC Prep\New Lists` folder, read on
2026-10-08. Every count and column name below came from the files, not from
description. Where I am inferring rather than reporting, I say so.

---

## 0. The objective, in Jarrod's words

> "A single portal where you can maintain work related to your compliance
> journey."

A list is primarily **a document you keep**: editable in the browser,
versioned, exportable for an assessor, and included in the point-in-time
bundle with the zipping and hashing already built. Population from WinGRC
activity — an approved asset appearing in `3.1.1c Authorized Devices` —
is an enrichment on top of that, wanted but not the foundation.

Read §6's ordering against that. The parts that make a list a kept,
defensible document come first; the scope-graph join comes last.

## 1. What is actually there

```
New Lists/            44 workbooks, 83 sheets, 14 control families
New Baselines/        29 .docx
New Policy Templates/ 19 .docx
New Procedure Templates/ 15 .docx
New Plan Templates/   10 .docx
New Supplimental Documents/ 10
```

Plus `Archive/` and `Changelog/` trees mirroring the same six categories.

The 44 list workbooks reference **64 distinct NIST 800-171 practice ids**
between them — so this library already covers more than half the 110
practices with an identify/define artifact.

## 2. The finding that reshapes the feature

`catalog.py` says: *"Catalog of CMMC lists as views over the scope graph.
Each ListView is a saved projection."* That is true of the four lists it
defines. It is **not** true of most of this library.

Counting the 83 sheets by what their rows actually are:

**Projections over the scope graph** — rows that are, or should be,
`scope_entity` rows:

- `3.1.1a/b/c` + External Services (the four that exist today)
- `CM/3.4.1a Hardware` and `3.4.1b Software`
- `AC/3.1.18 Mobile Device Register`
- `AC/3.1.5a Privileged accounts` (169 rows)
- `IA/3.5.1 User and Device Identifier Register`
- `AC/3.1.20 External Systems and Connections`
- `SI/3.14.2 Malicious Code Protection Inventory` (asset × agent)

Roughly ten sheets.

**Standalone registers and logs** — rows that exist nowhere else in the
system and never will: Configuration Change Log, Incident Tracking Log,
Risk Register, POA&M, Visitor Log, Media Sanitization Log, Training
Roster, Vulnerability Tracker, Flaw Remediation Tracker, Audit Log Review
Log, Ports/Protocols/Services Register, Application Allow-Deny, Crypto
Inventory, Boundary and Firewall Rule Register, Separation of Duties, and
the rest.

Roughly seventy sheets.

**So the standalone register is the dominant case, about seven to one, and
the current model treats it as the case that does not exist.** The model
has to inflect: a list is a definition plus its own rows, and "some or all
of these rows are projected from the scope graph" becomes a property of
certain lists rather than the premise of all of them.

This is the single decision this plan turns on. Everything below assumes
it.

### 2a. Worked example from real data: `3.1.1a` rows are accounts, not people

Added 2026-10-09, from a live dry-run of Jarrod's populated WinsorLabs
`Authorized-Entities.xlsx` (md5 `be72ba10…`) against the Winsorlabs org.

The `3.1.1a Authorized Users` tab is not one table. It is about 25
sections: Active Directory, Microsoft 365 Commercial, Physical Access, then
one per tool (Datto RMM, Liongard, Duo, Huntress, Tailscale, …). Each has
its own title row and header row, and the columns differ by tool (Role,
MFA Method, Tenant Scope, …). `Jarrod Winsor` appears in about 25 of them.

The current workbook importer assumes one header per tab and maps the tab
onto `scope_entity` PERSON. It produced 32 NEW "persons", including every
section title and the header row `First Name Last Name`. Jarrod's ~25 rows
would have collapsed into one entity, keeping the last section's columns.

But collapsing is not the bug to fix, because **those rows are not
people.** They are *accounts*: one person's Datto RMM account, M365
account, Liongard account. The row grain of `3.1.1a` is **person × tool
account**. Mapping it onto `scope_entity` PERSON is wrong at the root, and
no key-derivation fix rescues it. It is §4.1's shape (one list, a Tool
column) inside a single tab rather than across 25 sheets. It is also the
clearest real-data case for §2: even a tab that *looks* like a projection
over the scope graph is, at its natural grain, a register.

**Decision (2026-10-09):** the workbook importer is not taught to read
multi-section tabs. Its scope is the Authorized-Entities *sample* shape.
Jarrod's library is L.1's importer's job, and the two converge rather than
coexist. The apply of this file is deferred to L.1.

## 3. The template convention is the importer

Forty of the forty-four workbooks share an exact, machine-readable header
block. From `RA/3.11.1/Risk Register.xlsx`:

```
row 1   Risk Register
row 2   CMMC Practice / NIST 800-171: RA.L2-3.11.1
row 3   HOW TO USE THIS TEMPLATE
row 4   What goes here: Identified risks to CUI operations/assets, ...
row 5   Responsible: Customer Risk Owner / MSP / GRC partner
        |  Review cadence: Annually and on significant change
row 6   Replace every [PLACEHOLDER] with customer-specific data before use.
row 8   Updated Date:        Updated By:
row 9   Organizational Risk Register
row 10  Risk ID | Risk Description | Affected Asset/Process | Likelihood | ...
row 11+ data, with bare [PLACEHOLDER] example rows (see the correction below)
```

Every field a list definition needs is in the file:

| List definition field | Source |
|---|---|
| title | row 1 |
| control tags | row 2, already multi-valued (`CM.L2-3.4.3 / 3.4.4`) |
| description | row 4 |
| responsible role | row 5 |
| review cadence | row 5 |
| columns | row 10 |
| rows | 11+ |

**Nobody hand-authors 50 `ListView`s.** The importer reads them. That
resolves the code-versus-table question from the other direction: the
definitions are data because they arrive as data.

Note the convergences, and do not build parallel mechanisms for them:

- `Review cadence` is N.3's `cadence_months`, on a list instead of a
  document.
- `Responsible` is `ContactDocumentationRole`'s vocabulary.
- `[PLACEHOLDER]` and `[PLACEHOLDER - reason]` are N.4's two placeholder
  conventions -- see the 2026-10-10 correction below; they are not the same
  thing. (Was: "`[PLACEHOLDER - reason]` is the convention already recorded for N.4's
  variables.")
- Multi-valued row 2 is `ListView.control_ids`, which is already a tuple.

**Four non-conforming files**, all from an earlier generation (they carry a
`CMMC_` filename prefix): `3.1.15 Remote Privileged Execution`,
`3.1.20 External Connections`, `3.1.7a Privileged Functions`,
`MA/CMMC_372_Controls`. Do not write a second parser for four files.
Normalize them into the convention by hand, once, and let the importer be
one code path. Record that decision.

**Correction, 2026-10-10: two placeholder conventions, at two lifecycle
stages.** An earlier version of this plan called `[PLACEHOLDER - reason]` a
*template* convention. L.1's import of the real library showed it is not:
the 44 blank templates carry only bare `[PLACEHOLDER]` (zero reason-form
cells across all 45 imported lists), and the reason form appears only in
*filled-in* workbooks (the populated WinsorLabs `Authorized-Entities.xlsx`).

| Form | Where | Means | N.4 substitution must |
|---|---|---|---|
| `[PLACEHOLDER]` | blank templates | "fill this in" | resolve it (or report it unresolved) |
| `[PLACEHOLDER - reason]` | filled documents | "knowably absent, and here is why" | leave it alone, verbatim |

They are not one convention, so treating them as one would make N.4 either
strip a stated gap or leave a blank unfilled. L.1 stores both verbatim and
keys rows on neither.

## 4. De-duplication — the concrete targets

Jarrod asked for lists that are not duplicated but available under several
controls. There are four distinct kinds of duplication here and they want
different treatment:

**4.1 One list partitioned across sheets.** `AC/3.1.7a Privileged
Functions` has **25 sheets** — M365, Fenixpyre, Heimdal, DW Spectrum,
UniFi, Senteon, RoboShadow, Fortinet, Datto RMM, RocketCyber, SaaS Alerts,
IT Glue, Liongard, Duo, Domotz, Comet, CyberHoot, Evo, Autotask, Azure,
Intune, Defender XDR, Huntress, ImmyBot, Tailscale, FortiClient — all with
**identical columns**: Function Name, Function Description, Privileged?,
Required RBAC Role, Authorized User(s), Audit Log Location, Last Reviewed,
Notes.

That is one list with a Tool column, not twenty-five lists. Collapsing it
is 25 → 1, and it makes the list filterable by the tenant's actual
`OrgProduct` set — which is also N.5's suggestion input.

**4.2 Wide where it should be tall.** `SC/3.13.1 User & System Management
Functionality` is 68 columns: the triple (Function Name, Description,
Related Systems) repeated across the sheet. `AC/3.1.5c` does the same with
six columns. Those are rows wearing a costume. Normalize on import.

**4.3 Genuinely competing files.** `AC/3.1.20` holds two workbooks for one
practice — `CMMC_3.1.20_External_Connections.xlsx` (old generation) and
`External Systems and Connections.xlsx` (current). Jarrod picks one; the
importer should refuse to guess and report the collision.

**4.4 Overlapping content under different controls.** `CM/3.4.1a Hardware`
and `AC/3.1.1c Authorized Devices` are both device inventories with
different column sets. These are the real "same list, two controls" case,
and they are what the control tags exist for: **one device list, tagged
`AC.L2-3.1.1` and `CM.L2-3.4.1`, rendering the union of columns.** Do not
merge them by guessing — surface the overlap and let a human confirm.

Expect roughly **45–50 distinct list definitions** out of 83 sheets once
4.1 and 4.2 are collapsed. That is an estimate from the column analysis,
not a measured figure; the import's dry run will produce the real number.

## 4a. Cross-cutting rules — these are not negotiable per slice

**Version from day one. Do not retrofit it.** The document library plan
states this and roadmap item P is why: baseline mappings were built mutable,
approved tenants inherited silent rewrites, and versioning had to be added
later across five tables and two migrations. A list is the same class of
artifact — a record an assessor reads — and a list is *worse* if it goes
wrong, because the list often **is** the evidence rather than pointing at
it. `list_version` is append-only from the first migration. Editing rows
creates a version; it never mutates one.

**Point-in-time, always.** An exported or bundled list is the list as it
stood, never a live re-query. Today's state must never rewrite yesterday's
record.

**Candidates, never auto-applied.** Holds for evidence linking (§5) and for
any future population from WinGRC activity (§6, L.6).

## 5. Lists as evidence, and lists in the bundle

`Evidence`'s own docstring already states the principle: *"An artifact is
stored once and can satisfy multiple control objectives via
EvidenceStateLink (evidence minimization)."* A list is that artifact, and
its control tags are the set of `control_state`s it can link to.

Three constraints:

- **A populated list is a candidate, never an automatic link.** Same rule
  as tool activation, connector output and document publishing. A person
  attaches it.
- **Define "populated" and defend the definition.** My recommendation: a
  list with rows, none of which is still a `[PLACEHOLDER]` example. State
  it in code and show it in the UI, because an operator will otherwise not
  know why their list is not offered.
- `Evidence.artifact_type`'s CHECK currently allows `screenshot, export,
  document, link, policy, network_diagram, data_flow_diagram`. Decide
  whether a list is a new type or an `export`, and migrate the constraint
  if it is new.

**The bundle already has the machinery — lists become another
contributor, not a new mechanism.** Verified in `bundle_service.py`:

- Everything is copied to frozen dataclasses before rendering, so the
  bundle is coherent even if the live data is edited mid-export.
- SHA-256 per embedded artifact.
- `artifact_log.txt` lists every embedded file as `Algorithm | Hash |
  Path`.
- A second-order SHA-256 of `artifact_log.txt` appears on the cover page
  under the eMASS labels *Hashed Data List* / *Hash Value*.

A rendered list joins that flow: embedded, hashed, logged, covered by the
second-order hash. The work is adding lists to the snapshot and the
contributor set — **not** writing export, zip or hash code, which exists
and is already guarded by the determinism work.

Two things to get right when they join:

- The contributors query must have an explicit `ORDER BY`. The bundle
  determinism slice found exactly this defect and added static guards;
  a new contributor is the obvious way to reintroduce it.
- The bundle must embed the **list version** that was current at export,
  recorded by id, so the bundle names what it contains rather than what
  the list says today.

## 6. Slices

Ordered so that a list is a kept, defensible document as early as
possible, per §0.

**L.1 — the model and the importer.** Generic list definition + rows +
control tags, many-to-many, **versioned from this migration** (§4a).
Import a folder tree driven by the §3 convention, dry-run first, report
the §4 collisions rather than resolving them. No UI beyond what proves the
import.

**L.2 — the screen, and editing.** Browse by control family or by list,
view and edit rows, with audit and with every edit creating a version.
Merged with what was going to be a separate editing slice: a list you can
see but not edit is not yet the portal, and versioning is already in the
model from L.1. **✅ Done 2026-10-13** -- Library › Lists is the one Lists
screen (Scope › Lists folded into it); a stale save re-applies the second
editor's draft onto the newer version for review. See `docs/roadmap.md`'s
L.2 entry, including what the real library showed.
**L.2.1 ✅ Done 2026-10-14** -- the index keeps live, kept and template
lists in separate sections, and the merged device template is named for
its breadth with its added columns in the first screen. See the roadmap's
L.2.1 entry.
Since 2026-10-14 every list version also says why it exists -- a person's
edit, a changed source, or a change in WinGRC's importer with the source
unchanged -- in its version history on screen; see the roadmap's "List
versions say why they exist" entry, including the version-churn pattern
importer changes cause.
An import also never replaces a person's edit unless that exact edit
is named (`--overwrite-edit list_key@vN`); see the roadmap entry.
**L.2.2 ✅ Done 2026-10-15** -- the List Builder (the header block's
fields, v1 `created_by_hand`) and tags edited by hand, each recorded in
`list_tag_event` and shown in the list's History beside its versions; an
import manages only the tags it added. See the roadmap's L.2.2 entry.

**L.3 — export and bundle inclusion.** Export a list for an assessor, and
make lists first-class contributors to the point-in-time bundle per §5.
This is the slice that makes the library worth keeping things in, and it
is mostly wiring into machinery that exists. **✅ Done 2026-10-14** -- the operator
chooses the lists at export (none by default, until L.4 links them as
evidence); each is embedded as its current version, named by id. See
`docs/roadmap.md`'s L.3 entry.

**L.4 — evidence linking. ✅ Done 2026-10-17** (bench-verified; live
pending) -- a list version, offered when populated, attached by a person,
confined to its tags; drift said, moved forward deliberately; and the
export now carries What goes here, Responsible and Review cadence (item
T). See `docs/roadmap.md`'s L.4 entry, including why an export is not
made re-importable as a template. §5's first half. Small, and it closes
the loop from list to control. **It must honour the tag-removal rule decided in
L.2.2:** removing a tag is refused while evidence rests on it, naming the
link. Make `list_library.tag_removal_blockers` return every non-archived
EvidenceStateLink from the list's evidence to a control_state of that
control; `tests/test_list_builder.py` fails until a new list-referencing
table is settled that way. **And it must offer only populated lists,
using `list_library.is_populated`** (defined in L.7 from section 5's
recommendation: rows, none still carrying an uppercase fill-in token such
as `[PLACEHOLDER]` or `[CUSTOMER_NAME]`) -- not a
second rule.

**L.5 — review cadence.** Read from the template's row 5, reusing N.3's
notifier rather than building a second one. Deliberately after L.3/L.4:
cadence on a list nobody can export yet is premature.

**L.7 — adopting a template ✅ Done 2026-10-16** (ran before L.4, by
agreement: linking an adopted, filled-in list is the real L.4 workflow).
An org copies a template from the org that holds it -- templates stay
org-scoped -- into its own lists; the copy records the template and
version (`template_list_id`, `template_list_version_id`), its v1 is
`adopted_from_template`, and a newer template version is shown as
available and taken only deliberately, never over a person's edit unless
that edit is named. See the roadmap's L.7 entry.

**L.8 — the list page puts the rows first ✅ Done 2026-10-16.** Frontend
only. See the roadmap's L.8 entry.

**L.10 — the screens say what is true ✅ Done 2026-10-18 (verified on
live).** Found by Jarrod within ten minutes of L.4's first use on
live: an unfilled list drawn like an attached one, a tag dialog stating
the rule rather than checking it, and a History that could not say
whether the list had ever been attached. Migration 0068
(`list_link_event`). See the roadmap's L.10 entry.

**L.9 — the scope-drawn lists look like the rest. ✅ DONE (2026-10-20).**
The four lists drawn from scope now draw their rows in the library list
page's table. `components/ListRows.tsx` (`ListRowsTable`, `useSideScroll`)
and one `.list-rows` CSS block are imported by both panels, not copied.
See the roadmap's Done entry "The visual batch: V, L.9 and U".

**L.6 — the scope-graph join.** The ~10 projected lists draw rows from
`scope_entity` — asset approval appearing in `3.1.1c`, and the rest. Last
deliberately: it is the hardest part, it is 12% of the library, and §0 says
it is enrichment rather than foundation. When it lands, the same rule as
everywhere else applies — a synced row is a candidate a person accepts,
and it never overwrites a hand-entered field it has no source for.

L.1 before anything else, and L.1's dry run is what makes §4's estimate a
fact.

## 7. What this does to the roadmap

N.4 (MSP templates) was scoped around documents. The 83 `.docx` files in
`New Baselines`, `New Policy/Procedure/Plan Templates` and
`New Supplimental Documents` are still N.4's subject and still want the
`[PLACEHOLDER]` substitution work. Lists are a different artifact with
different storage, and splitting them out as item L rather than folding
them into N is the right call.

N.5's suggestion engine gains a second output: suggested *lists*, driven by
the same profile and `OrgProduct` inputs. The 3.1.7a tool partition in §4.1
is the obvious first rule — a tenant without Tailscale does not need the
Tailscale privileged-functions rows.

## 8. Answered, 2026-10-10 — these are settled, not open

**`Archive/` and `Changelog/`: do not ingest Archive as v1.**
Verified by reading them. `Archive/Lists` holds six files only — AC 3.1.1,
3.1.2, 3.1.3, 3.1.4, 3.1.5 and SC 3.13.1 — all dated May, all superseded
by a `New Lists` file at the same control path. `Changelog/Lists` holds
six hand-written markdown changelogs with dates, authorship and rationale
(e.g. *"Replaced generic example rows with real FenixPyre evidence, CMVP
#4825"*).

They are genuine prior versions, but of six of the forty-four, and the
changelogs describe several revisions between May and June. Importing the
May files as v1 and the June set as v2 would assert a two-step history
that did not happen — a fabricated record in a product whose purpose is
defensible records.

So: **import `New Lists` as v1.** Attach the matching changelog markdown
as a provenance note where one exists. Record the six Archive files as
deliberately not ingested, with their path, so it reads as a decision
rather than an oversight. Ingesting them later is a six-file follow-up.

**3.1.20 collision: `External Systems and Connections.xlsx` wins.**
It conforms to the §3 template convention; the `CMMC_`-prefixed file is
one of the four older non-conforming ones. The loser goes to `Archive/`
rather than being deleted.

**Device inventories: one list, two tags.** `CM/3.4.1a Hardware` and
`AC/3.1.1c Authorized Devices` become a single device list tagged
`AC.L2-3.1.1` and `CM.L2-3.4.1`, rendering the union of columns. This is
the de-duplication the control tags exist for and the strongest test of
the model — if it does not work here it does not work.

**These are MSP-level templates, not tenant data.** The changelog states
each file ships with "2–3 illustrative `[PLACEHOLDER]` example rows." The
template-versioning model from the document library plan carries over
unchanged: a client copy records the template version it came from, and a
master edit creates a new version that client orgs adopt deliberately.

**The device union's column mapping (decided 2026-10-10, during L.1).** The
two sheets do not clash under one name; they hold the *same facts under
different names and granularity*, so a literal union would store five facts
twice. Jarrod chose one list on 3.1.1c's finer-grained schema (Make and
Model separate, OS and BIOS separate) plus two columns with no equivalent:

| 3.4.1a Hardware | becomes |
|---|---|
| Asset Name | Name |
| Owner / User | Owner / Primary User |
| Make / Model | Make |
| Serial / Asset Tag | Serial # or Asset Tag |
| OS / Firmware | OS |
| Location | Location |
| Type *(device class)* | **Device Type** *(added -- not 3.1.1c's Asset Type, a CMMC category)* |
| Baseline Ref | **Baseline Ref** *(added)* |

3.4.1a's example rows are not carried: they are illustrative, and its
combined columns would land half-mapped. Recorded in code as
`list_templates.DEVICE_MERGE`; an absorbed column with no recorded mapping
fails the import rather than being guessed.
