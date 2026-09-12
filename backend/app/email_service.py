"""Outbound email — the one module every caller sends mail through.

**Content rule (non-negotiable): notification emails carry no compliance
content.** No control ids, no findings, no evidence detail, no asset
names, no assessment results, no scores — not in the body, and not in
the subject line either, since a subject transits third-party mail
servers in cleartext the same as the body. The pattern every caller must
follow is "something needs your attention, sign in to WinGRC" plus a
link. Reason: email lands unencrypted in a mailbox outside this system's
audit boundary. A tool whose job is protecting CUI must not leak
CUI-adjacent detail through its own notifications — that would be a
self-inflicted finding in the exact domain the product exists to serve.
This module has no way to enforce that on a caller's `subject`/`body`
strings; every call site is reviewed for it instead (see
routers/users.py's invite/reset call sites for the only two that exist
today — this is a hard rule for anyone adding a third).

**Fails clearly and safely when email is not configured.** A fresh
deployment has no SMTP set up, and most of this app works fine without
it — nothing that calls `send()` may 500 or lose data because mail
isn't configured. `send()` never raises for "not configured" or "the
provider rejected it" — it returns an `EmailSendResult` the caller reads
(`.sent`, `.error`). It raises only for a genuine programming error (a
bad argument), never for network/provider conditions, which are the
common, expected case for a feature this optional.

**Synchronous, on purpose, not fire-and-forget — read this before
"fixing" it.** Every sync endpoint in this app is already dispatched
onto anyio's worker threadpool (see config.py's pool-sizing comment), so
calling smtplib synchronously here does not block the event loop the way
the get_current_user incident did; it occupies one worker-threadpool
slot for the duration of the SMTP round-trip, bounded by
connectors/smtp.py's own short timeout. That's a real cost under
concurrent load, and invite/reset are low-frequency admin actions, not a
hot path — the tradeoff is deliberate, not overlooked. The honest
alternative to "block briefly, synchronously, with the result visible in
the response" is a background send, and a background send has no retry
and no delivery record: for a password reset or invite, a
fire-and-forget failure is a silent failure a real user experiences as
"I never got the email," with no admin-visible signal that anything went
wrong at all. The real fix is a queue a future job-scheduler slice
drains — send() is written so a queue worker can call it unchanged, a
plain function taking a session and returning a result, with nothing
request-specific baked in — but that queue does not exist yet, and a
synchronous send whose failure the caller sees immediately is a more
honest v1 than a background send that can vanish silently. Do not
convert this to fire-and-forget without also building the delivery
record and retry that make a queue trustworthy; see docs/roadmap.md's
"Outbound email — what this slice leaves open" entry.

Never logs the credential, the full message body, or any token — logs
the recipient, a template name, and the outcome only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.smtp import SmtpConfigError, SmtpConnectionError, open_connection, parse_settings
from .crypto import CredentialCipherError, decrypt_credential
from .models import IntegrationConnection

logger = logging.getLogger(__name__)

_CONNECTOR_KEY = "smtp"


@dataclass(frozen=True)
class EmailSendResult:
    sent: bool
    # None when sent=True. A short, admin-facing explanation otherwise --
    # "not configured" or the specific SmtpConnectionError/SmtpConfigError
    # message. Never the credential; connectors/smtp.py's own error
    # messages already exclude it by construction.
    error: str | None = None


def _load_credential(session: Session) -> tuple[dict, dict] | None:
    """Returns (config, credential) for the configured SMTP connector, or
    None if it isn't configured at all -- the common, expected case on a
    fresh deployment."""
    row = session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == _CONNECTOR_KEY)
    ).first()
    if row is None or row.encrypted_credential is None:
        return None
    credential = json.loads(decrypt_credential(row.encrypted_credential))
    return row.config or {}, credential


def send(session: Session, *, to: str, subject: str, body: str, template: str) -> EmailSendResult:
    """Sends one plain-text email. `template` is a short machine name
    (e.g. "user_invite", "password_reset") for logging/audit only -- never
    shown to the recipient.

    Returns EmailSendResult; never raises for "not configured" or any
    network/provider failure. Callers must always keep a non-email
    fallback for whatever `send()` was meant to deliver (see
    routers/users.py's invite/reset-password call sites) -- this
    function's whole contract is "tell you honestly whether it went out,"
    not "guarantee it does."
    """
    try:
        loaded = _load_credential(session)
    except CredentialCipherError as e:
        logger.warning(
            "Email not sent (credential decrypt failed): to=%s template=%s", to, template
        )
        return EmailSendResult(sent=False, error=f"Could not decrypt stored SMTP credential: {e}")

    if loaded is None:
        return EmailSendResult(sent=False, error="Email is not configured for this deployment.")
    config, credential = loaded

    try:
        settings = parse_settings(config, credential)
    except SmtpConfigError as e:
        logger.warning(
            "Email not sent (bad SMTP config): to=%s template=%s error=%s", to, template, e
        )
        return EmailSendResult(sent=False, error=str(e))

    msg = EmailMessage()
    msg["From"] = (
        f"{settings.from_name} <{settings.from_address}>"
        if settings.from_name
        else settings.from_address
    )
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        smtp = open_connection(settings)
    except SmtpConnectionError as e:
        logger.warning(
            "Email not sent (connection failed): to=%s template=%s error=%s", to, template, e
        )
        return EmailSendResult(sent=False, error=str(e))

    try:
        smtp.send_message(msg)
    except Exception as e:  # noqa: BLE001 -- any send-time failure is reported uniformly
        logger.warning(
            "Email not sent (send failed): to=%s template=%s error=%s", to, template, e
        )
        return EmailSendResult(sent=False, error=f"{settings.host} rejected the message: {e}")
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001 -- best-effort cleanup only
            pass

    logger.info("Email sent: to=%s template=%s", to, template)
    return EmailSendResult(sent=True)
