# Product baseline library

One YAML entry per security product. Each declares, per control, what the
product covers when configured, the assumed config, the responsibility split,
and the *minimum* evidence needed. These entries are the shared reference data
that pre-populates control state when a tenant marks a product in-use.

## Classification (drives engine behavior)

- `provider_satisfies` — product materially meets the control for in-scope
  assets → flip to `pending_evidence`, queue evidence.
- `shared` — product enables it; customer owns a configurable half.
- `customer_owns` — product explicitly does NOT do this → do not credit it;
  route to the product that actually owns it.

## Evidence minimization rules

- One artifact may satisfy many objectives — capture once, reference many.
- Prefer one authoritative export over many screenshots.
- Only `provider_satisfies`/`shared` controls generate provider evidence tasks.
- Capture product-level config once, reuse across tenants; re-capture only
  tenant-specific state.

## Entries

- `rocketcyber.yaml` — Managed SIEM + SOC (Kaseya). Worked example; note the IA
  family is correctly held as `customer_owns` despite appearing in the vendor
  CRM.

## Provenance

Entries are derived from vendor capability/CRM docs (which describe product
behavior, not customer data). Do **not** commit customer-specific evidence or
environment details here — those belong to a tenant instance, never the shared
library.
