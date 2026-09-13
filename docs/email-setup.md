# Email setup (outbound SMTP)

WinGRC sends outbound mail for a small, fixed set of notifications —
account invites, password resets, the annual SPRS-submission reminder, and
the periodic review/attestation workflow. None of these carry compliance
content (no control IDs, no findings, no evidence, no scores — see
`backend/app/email_service.py`'s own module docstring for the content
rule this codebase holds to without exception); every one of them is a
"something needs your attention, sign in to WinGRC" message plus a link.

This page covers configuring outbound email for a self-hosted deployment.
It is written against **generic SMTP** — any provider works, not just one
vendor — but uses **SMTP2GO** as a concrete, confirmed-working example
throughout, because a real worked example is worth more than an
abstraction and this one is verified against the live service
(2026-09-13).

If nothing here is configured, WinGRC still works: every email-sending
action falls back to returning the token/link in the response body for
the admin to deliver by hand (see `email_service.py`). Configuring SMTP
makes that automatic; it is never required to use the product.

## Encryption mode and port — read this first

This is the mistake people will actually make, so it goes first, not last.

SMTP's "encrypted or not" choice is really three choices, not two:

| Mode | What it means | Typical port(s) |
|---|---|---|
| **STARTTLS** | Connect in plaintext, then upgrade to TLS before any credentials or mail data are sent | 587 (standard), plus provider alternates like 2525 or 8025 |
| **Implicit TLS** | TLS from the very first byte — there is no plaintext phase at all | 465 (standard), plus provider alternates like 8025 |
| **None** | Unencrypted, full stop | whatever the server publishes — internal/trusted relays only |

**The port and the mode must match, or the connection fails immediately
and confusingly.** A client that opens a STARTTLS port (587, 2525) but is
told to speak implicit TLS from byte one will get a TLS handshake error,
because the server is still speaking plaintext and expecting the client
to ask for an upgrade. The exact symptom, verbatim, from a real
misconfiguration on 2026-09-13:

```
Host: mail.smtp2go.com  Port: 2525  Encryption Mode: tls
→ Could not connect to mail.smtp2go.com:2525 —
  [SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)
```

Port 2525 is SMTP2GO's STARTTLS alternate port — it wants
`encryption_mode = starttls`, not `tls`. `tls` reads as "yes, encrypt
this," which is the intuitive choice for someone who wants encryption,
and it is the wrong choice for three of SMTP2GO's four published ports.
This is a UI defect, not user error — the Administration → Email screen's
Encryption Mode field is a labeled dropdown for exactly this reason,
naming what each option does and its typical port(s) rather than just
showing the raw setting name. If you land on this page after hitting
`WRONG_VERSION_NUMBER` yourself: switch Encryption Mode to **STARTTLS**
if you're on port 587/2525/8025-for-starttls, or to **Implicit TLS** if
you're on 465. The port field itself stays free text on purpose —
providers publish alternate ports specifically to work around networks
that block the standard ones, so a fixed dropdown of ports would just
recreate this same class of bug in a different field.

## `WINGRC_PUBLIC_URL` must be set too

This is easy to miss because it isn't an SMTP setting at all — it's a
deployment-level environment variable — but a deployment with fully
working SMTP and no `WINGRC_PUBLIC_URL` still can't send a usable email,
because every notification WinGRC sends contains a link back into the
app, and the backend has no other way to know its own public hostname
(nginx terminates the real hostname; there's no in-flight request to
derive it from when a background job sends mail). Without it, the invite
and password-reset paths fail closed with an honest, specific error —
confirmed live on the first real deployment (wl-util-1, 2026-09-13),
which hit exactly this before SMTP was even configured:

```
"Email link cannot be built: WINGRC_PUBLIC_URL is not configured."
```

Set it in your `.env` alongside your SMTP setup, not as a separate later
step:

```
WINGRC_PUBLIC_URL=https://your-deployment-hostname.example
```

See `.env.example`'s own comment on this variable for the full picture
(what it's used for, what happens when it's unset). Leaving it unset is a
legitimate deployment choice — invite/reset still work, just via a
manually-delivered token instead of a clickable link — but it is *not* a
legitimate choice for a deployment that also wants working SMTP
notifications; the two settings only add up to working email together.

## Where the credential lives

Unlike `WINGRC_PUBLIC_URL`, the SMTP credential itself is **not** an
environment variable. It's entered through Administration → Email in the
app, and stored encrypted at rest in WinGRC's own Postgres database
(`backend/app/crypto.py`, Fernet symmetric encryption) — the same pattern
the Liongard connector uses, and for the same reason: a self-hosted
deployment's operator (the MSP) holds their own third-party credentials
directly, rather than handing them to WinGRC-the-project in a config
file that might get committed or shared by accident.

The encryption key that protects it — `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`
— **is** an environment variable, and its custody matters: losing it
means every credential encrypted under it (SMTP included) becomes
permanently undecryptable, with no reset and no backdoor — only
re-entering the credential by hand once a new key is in place. See
`.env.example`'s own comment on `WINGRC_CREDENTIAL_ENCRYPTION_KEYS` for
the full guidance (where to store it, how to rotate it with
`wingrc rotate-credential-keys`). If you're setting up SMTP for the first
time, this is the moment to make sure that key is durably stored
somewhere outside the deployment itself — a password manager, alongside
your Postgres/MinIO secrets — not just sitting in `.env` on one box.

## Worked example: SMTP2GO

This is one working configuration, not the only one. Any SMTP provider —
a corporate relay, Postmark, SES, a self-hosted Postfix — works the same
way through the same form.

| Field | Value |
|---|---|
| Host | `mail.smtp2go.com` |
| Port | `2525` |
| Encryption Mode | STARTTLS |
| Username / Password | your SMTP2GO SMTP user credentials |
| From Address | an address on **your own, verified** sending domain — e.g. `noreply@yourdomain.example` |

SMTP2GO also publishes 587 (standard STARTTLS) and 8025 (STARTTLS
alternate) as equivalents to 2525, and 465 for Implicit TLS if you'd
rather use that mode instead. Pick whichever port your network doesn't
block; they're interchangeable as long as the Encryption Mode matches.

**Never put a real credential in this document or in any WinGRC
documentation** — not even one that looks obviously fake. This page
describes the shape of a working configuration; the actual username and
password are yours to enter directly into Administration → Email, where
they're encrypted immediately and never displayed again (only a masked
last-4-characters hint is shown afterward).

## Testing your configuration

Administration → Email → **Test connection** connects, negotiates the
TLS handshake for whichever mode is configured, and authenticates if a
username is set — no message is sent. This alone catches the large
majority of real misconfigurations: wrong host or port, the encryption-
mode mismatch above, wrong credentials. Its error messages are
specific on purpose (which step failed: connect, TLS handshake, or
auth) — if something's wrong, the message should tell you which of
those three it is, not just "connection failed."

You can also enter a recipient address in the same screen and send one
real test message to it. Do this deliberately, once you believe the
configuration is right — the address is never remembered between test
runs and is never inferred from your From Address, so it's always a
conscious choice to send. A successful send is reported as **"accepted
by the provider — check the inbox to confirm delivery,"** never as "sent
successfully" — see the next section for why that distinction matters.

If the connection and authentication succeed but the test message itself
is rejected, that's reported separately, with its own explanation — most
often, this means the provider accepted the credentials but refused to
relay mail *as your From Address's domain*, which is exactly the
deliverability gap the next section covers.

## The part most likely to be missed: a green checkmark is not proof mail will arrive

**Connection success and mail deliverability are two different things,
and this page's testing tools can only verify the first.**

Test-connection (with or without a test message) proves: the server is
reachable, the TLS handshake for your chosen mode works, and your
credentials are accepted. It does **not** prove that the provider will
accept mail sent *as your domain* to arbitrary recipients, and it says
nothing about whether those recipients' own mail systems will accept or
spam-folder it.

Sending as `you@yourdomain.example` generally requires completing your
provider's domain verification — adding SPF and DKIM DNS records so
receiving mail servers can confirm the message really came from a source
your domain authorized. For SMTP2GO, this means adding the CNAME records
they provide in their dashboard for your sending domain. **Without this,
a real send can succeed at the SMTP level (the provider accepts it,
`test connection`'s send-a-message option reports success) and still be
rejected, spam-foldered, or silently dropped downstream** — after the
green checkmark, not instead of it.

This documentation won't reproduce provider-specific domain-verification
steps, because they change and go stale independently of WinGRC — go to
your provider's own current documentation for the exact DNS records to
add (search "[your provider] SPF DKIM domain verification").

**Before relying on WinGRC's email notifications for anything real,
send one actual test message to a mailbox you control and confirm it
lands in the inbox** (not spam, not rejected) — using the recipient field
described above. This is the only way to confirm delivery actually
works; a passing connection test is necessary but not sufficient.

---

*This page will move to the public documentation site (docs.wingrc.us,
roadmap item O) once that exists — see `docs/roadmap.md`'s entry for item
O, which lists this page's content as planned material for the move.*
