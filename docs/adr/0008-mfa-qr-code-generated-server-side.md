# 8. MFA QR code generated server-side, not by a third party

Date: 2026-08-06
Status: Accepted — implemented 2026-08-06, see `backend/app/routers/auth.py`
(`_mfa_qr_data_uri`, `mfa_enroll`), `backend/pyproject.toml` (`segno`
dependency), `frontend/src/components/MfaEnrollmentFlow.tsx`. Landed as its
own commit, separate from and before I.9 (`docs/PLAN-auth-rbac-completion.md`)
extracts and reuses the enrollment UI for self-service MFA re-enrollment.

## Context

`MfaEnrollmentFlow.tsx` — mounted by both `LoginPage` (first-time login) and
`InviteAcceptPage` (invite/reset redemption), the only two places TOTP
enrollment has ever happened in this codebase — rendered its QR code with:

```
<img src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(enrollData.provisioning_uri)}`} />
```

`provisioning_uri` is the `otpauth://` URI `mfa_enroll` builds from the
freshly-generated random TOTP secret (`routers/auth.py`, `pyotp.TOTP(secret)
.provisioning_uri(...)`). Rendering that `<img>` makes the browser issue a
real `GET` to `api.qrserver.com` with the full secret in the query string.

**This is third-party disclosure of a live authentication secret, not an
air-gap/deployment-fit issue.** `CLAUDE.md`'s air-gap requirement is what
made this visible — the request simply fails with no network path in an
air-gapped deployment — but that framing understates it. In any deployment
*with* internet access (the common case), the request quietly succeeds and
the secret quietly leaves the trust boundary to an operator WinGRC has no
relationship with, no data-processing agreement with, and no control over.
Anyone able to observe that request — the qrserver.com operator, any
logging/CDN layer in front of it, or anyone with access to client-side
network logs/browser history — has everything needed to generate valid
codes for that user's second factor indefinitely, with no way for WinGRC or
the user to detect it happened. **Every MFA enrollment this product has
ever completed has gone through this code path** — `MfaEnrollmentFlow` is
the only QR-rendering call site that has ever existed, checked directly
against the frontend source rather than assumed.

### Inventory: other outbound third-party requests

Asserting "this is the only leak" is itself a claim worth checking rather
than assuming, so before treating this as an isolated fix, every outbound
network call in both the frontend and backend was enumerated (`grep` for
`https?://` in `frontend/src`, and for `requests\.|httpx\.|urlopen|urllib`
in `backend/app`):

| Call site | Destination | What's sent | Assessment |
|---|---|---|---|
| `MfaEnrollmentFlow.tsx` (this ADR) | `api.qrserver.com` | Full TOTP secret, in cleartext, via `otpauth://` URI | **Defect — fixed here.** |
| `auth.check_pwned_password` (`auth.py`) | `api.pwnedpasswords.com` | First 5 hex chars of `SHA-1(password)` only | **Different risk profile — reviewed, no change.** This is the published HIBP k-anonymity protocol by design: the full password is never transmitted, never even hashed reversibly for transmission — only enough of a SHA-1 prefix to retrieve a same-prefix anonymity set (hundreds of candidate hashes), from which the caller checks the suffix locally. No secret, credential, or identifying data leaves the server. Also: gated behind `settings.pwned_passwords_check` (operator-disableable, e.g. for air-gapped deployments) and fails open silently on timeout/network error — already accounts for the no-network case this ADR's QR fix now also has to. Docstring already states this correctly ("the password itself is never transmitted"); the only fix this ADR makes is putting the comparison in writing here so both third-party calls are on the same record instead of only the safe one being documented.
| `sso_login`/`sso_callback` (MSAL, `routers/auth.py`) | The operator's own configured Entra ID tenant | Identity claims, per the standard OIDC flow | Not third-party disclosure in the sense this ADR is about — sending identity data to the IdP the operator explicitly configured for SSO is the entire point of the feature; the operator chose and controls that endpoint. |
| `storage.py` (boto3) | The operator's configured S3-compatible endpoint (MinIO in dev, Azure Blob/AWS S3 in cloud) | Evidence files, logos | Same reasoning — operator-chosen, operator-controlled infrastructure, not a fixed WinGRC-selected third party. |
| `ai/anthropic_.py` | Anthropic API, only if configured | Whatever the AI-drafting feature sends | Already governed by `CLAUDE.md`'s hard rule ("BYO-AI / pluggable provider... CUI-sensitive deployments must be able to keep generation local; never assume CUI may go to a commercial cloud LLM") — opt-in, deployment-configured, already documented. Not a silent default the way the QR call was. |

No other outbound call exists in either the frontend or backend beyond
these five. The QR case is the only one that (a) sends a secret rather than
non-identifying or operator-consented data, and (b) does so unconditionally,
with no opt-out and no deployment configuration involved — every other
row is either safe by protocol design or explicitly operator-chosen.

## Options considered

**A. Client-side generation** — bundle a JS QR-encoding library (e.g.
`qrcode`, `qrcode-generator`) and render from `provisioning_uri`, which the
browser already has. Eliminates the third-party hop with no extra round
trip. Cost: `frontend/package.json` has exactly two runtime dependencies
today — `react`, `react-dom`. Nothing else, ever; no date library, no icon
library, no UI kit. Adding a QR library would be the **first non-React
dependency this frontend has ever taken on**, and that near-zero footprint
reads as a deliberate choice, not an oversight.

**B. Backend SVG generation** — render the QR server-side (Python, pure,
no C extension) and return it as an inline `data:image/svg+xml` URI
alongside `provisioning_uri`/`secret` from the same `mfa_enroll` response.
Frontend change becomes a one-line `<img src>` swap, no new rendering
logic. Cost: `backend/pyproject.toml` already carries 13 real dependencies;
`segno` sits at the same weight class as `pyotp` (small, pure-Python,
single-purpose, already accepted as necessary for this exact feature) and
adds nothing to what ships to the browser.

**Decision: B (`segno`).** The frontend's near-zero-dependency posture is
the more deliberate and more unusual invariant of the two codebases —
worth protecting over avoiding one more backend dependency in a codebase
that already has a normal-sized one. It's also the smaller change:
`mfa_enroll` already computes the secret and URI server-side, so rendering
the QR there too is an extension of the same endpoint rather than new
client-side machinery. And it keeps the secret inside the server's trust
boundary by construction, the same principle `CLAUDE.md` already applies to
AI generation for CUI-sensitive deployments — never pushed to the edge when
a server-side option exists.

`_mfa_qr_data_uri()` (`routers/auth.py`) follows the same
lazy-import-with-501 pattern the file already uses for `pyotp` and
`msal` (`try: import segno / except ImportError: raise HTTPException(501,
...)`), for consistency rather than importing it eagerly at module load.

The existing fallback is untouched and still does its job: `.totp-manual`'s
`<details>` block (manual secret entry) exists precisely so a QR failure —
of any kind, from either source — isn't fatal to enrollment.

## Remediation for existing enrollments

The fix stops *future* disclosure. It does nothing for secrets already
disclosed. **Every TOTP secret enrolled before this fix was already sent to
api.qrserver.com and must be treated as compromised** — it is still valid,
still usable by anyone who captured that request to generate correct codes,
and there is no way to detect after the fact whether it was captured or
misused. This needs to be said plainly rather than left implicit: shipping
the code fix does not remediate any account enrolled before today.

The only real remediation is rotation — every existing local user with
`mfa_enrolled = True` needs to re-enroll MFA to get a fresh secret that was
never sent anywhere. The admin path to force that already exists:
`POST /orgs/{org_id}/users/{user_id}/reset-mfa` (`routers/users.py`,
`reset_user_mfa`) nulls `totp_secret`, deletes all backup codes, and sets
`mfa_enrolled = False` — the user's next login routes them back through
enrollment and gets a secret generated after this fix, never exposed to a
third party.

**That endpoint has a side effect worth calling out explicitly before
anyone reaches for it: it also sets `is_active = False`.** Triggering it
is not "please re-enroll at your convenience" — it locks the account out
immediately, and the user needs an admin-issued invite/reset token
(`reset-password`) to get back in and re-enroll, same as a brand-new
invite. An admin running this against a live user without knowing that
side effect would cause an unplanned, unannounced lockout.

For `wl-util-1` specifically: this is a small, known set of accounts on a
single dev/test box, not a fleet of production tenants — worth just doing
now rather than scheduling it. This ADR records the recommendation; running
`reset-mfa` against real accounts and coordinating the resulting
reset-token handoff is an operator action, not something this change
performs automatically.

## Consequences

- `backend/pyproject.toml`: `segno>=1.6` added to core dependencies.
  `wl-util-1` needs a dependency reinstall (`pip install -e .` or
  equivalent) after `git pull`, not just a code pull — this is a new
  package, not just changed source.
- `mfa_enroll`'s response gains `qr_data_uri`; `provisioning_uri` and
  `secret` are unchanged, so nothing else that reads this response breaks.
- `MfaEnrollmentFlow.tsx` and `frontend/src/types.ts` (new `MfaEnrollData`
  type) are the only frontend files touched.
- New DB-free unit test (`tests/test_auth.py`,
  `test_mfa_qr_data_uri_is_self_contained`) asserts the returned value is a
  `data:image/svg+xml` URI and contains no reference to `qrserver` — a
  regression guard against this reappearing later for convenience (e.g. a
  future contributor reaching for an external image service again without
  knowing why that was rejected here).
- I.9 (`docs/PLAN-auth-rbac-completion.md`) extracts the QR-display and
  backup-codes-display markup out of `MfaEnrollmentFlow` for reuse in
  self-service MFA re-enrollment. Landing this fix first means that
  extraction — and the new self-service `/auth/mfa/reenroll` endpoint —
  never has an `api.qrserver.com` code path to inherit in the first place.
