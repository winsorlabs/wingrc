# 3. Scope graph as the source of truth; lists are views

Date: 2026-06-26
Status: Accepted

## Context
CMMC requires many maintained lists (authorized users, devices, processes,
external services, software, data flows). Maintained as separate spreadsheets
they drift from reality and re-key the same data.

## Decision
Store one normalized **scope graph** (`scope_entity`: common fields + JSONB
attributes, isolated per tenant by `org_id` + RLS). Each CMMC list is a
**`ListView`** — a filter plus ordered columns — rendered on demand. Importers
normalize source rows into `CanonicalEntity` records; reconciliation produces a
reviewable diff that is applied only on confirmation; rendering projects a view
to an assessor-ready file with provenance.

## Consequences
Enter-once, no re-keying; the tool is the everyday source of truth, not just an
audit artifact. New required lists = a new `ListView`, not a new spreadsheet.
A single-table + JSONB model trades some strict typing for importer flexibility
and trivial list projection; typed Pydantic schemas per entity type recover
validation where needed.
