"""SMTP connector — the outbound-email prerequisite for D.3's approval
workflow (job scheduler is the other, deliberately not built here).

Generic SMTP, not SMTP2GO-specific — Jarrod uses SMTP2GO but explicitly
does not want it to be the only supported provider, so nothing here
assumes a particular vendor's quirks. `test_connection` and
`email_service.py`'s real send both go through `open_connection()` below,
so the connect/TLS/AUTH logic is written exactly once.

Encryption mode is a three-way choice, not a boolean: `use_tls: bool`
cannot express "STARTTLS on a plaintext-then-upgrade port" vs. "implicit
TLS from the first byte" vs. "no encryption at all", and providers
genuinely differ (SMTP2GO publishes 587/2525 for STARTTLS and 465/8025 for
implicit TLS as alternates for networks that block the standard ports).
Represented as `encryption_mode: "starttls" | "tls" | "none"` — a plain
string, matching this codebase's existing convention for small fixed
vocabularies (e.g. `Product.role`, `ControlState.status`) rather than a
new enum type.

Port is free-form text, not a dropdown — SMTP2GO's own fallback ports
(2525, 8025) already break the assumption that only 587/465 are in use.

Auth is optional: `credential_fields` includes `username`/`password`, but
both are in `optional_fields` (see connectors/__init__.py's ConnectorSpec).
An internal relay or a LAN Postfix may accept unauthenticated mail from a
trusted host — if `username` is blank, `open_connection()` skips AUTH
entirely rather than calling `.login("", "")`, which would either fail
against a server that doesn't expect AUTH at all or, worse, be silently
accepted by a misconfigured one.

Certificate verification defaults on (`ssl.create_default_context()`,
the stdlib's own verifying default). `verify_cert` is an explicit,
honestly-named escape hatch for a self-signed internal relay — never
silently implied by another setting, and it takes a real security
posture change to turn it off, not enabled by default.

test_connection() sends a message only if the caller passes a recipient
via `test_input` (2026-09-14, requested directly after Jarrod's live
SMTP2GO setup: connect-only testing had already done its job — catching
the STARTTLS-vs-implicit-TLS mismatch below — but didn't prove mail could
actually be delivered). With no `test_input`, the exact prior behavior
applies unchanged: connect + EHLO + the TLS handshake for the selected
mode + AUTH (if configured) + quit, no message sent, never surprising
whoever might be on the receiving end of a repeatedly-clicked "Test
connection" button. A recipient is never inferred or defaulted (not even
to `from_address`) — it must be typed deliberately every time, so a test
click never sends mail by accident.

The two outcomes past that point are deliberately reported differently:
a `MAIL FROM`/`RCPT TO`/DATA rejection (SmtpSendError below) is a
different operator problem from a connection or auth failure — typically
the sending domain isn't verified with the provider (SPF/DKIM), which is
precisely the gap connect-only testing can never see (a `250` from the
provider is not proof of delivery either — see docs/email-setup.md).

Encryption-mode mismatch, worked example: SMTP2GO publishes 587/2525 for
STARTTLS (plaintext connect, then upgrade) and 465/8025 for implicit TLS
(encrypted from the first byte). Configuring `encryption_mode: tls`
against port 2525 fails immediately with
`[SSL: WRONG_VERSION_NUMBER] wrong version number` — port 2525 speaks
plaintext until the client asks to upgrade, so a client that opens it
expecting TLS from byte one sees garbage where a TLS handshake should
start. This error message is deliberately kept specific (not swallowed
into a generic "connection failed") because it was the only reason that
misconfiguration was diagnosable at all; the fix that actually mattered
was making the encryption-mode *choice* self-explanatory in the form
(connectors/__init__.py's ConfigField/ConfigFieldOption), not the error
text, which already worked.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from . import ConfigField, ConfigFieldOption, ConnectorSpec, ConnectorTestResult

_TIMEOUT_SECONDS = 15
_VALID_ENCRYPTION_MODES = frozenset({"starttls", "tls", "none"})


class SmtpConfigError(Exception):
    """The stored config/credential itself is unusable (bad encryption_mode,
    missing host, etc.) -- distinct from SmtpConnectionError so callers
    can tell "nothing was even attempted" from "a real network exchange
    failed partway through"."""


class SmtpConnectionError(Exception):
    """A specific, human-readable explanation of what failed while talking
    to the SMTP server -- DNS/connect, TLS handshake, AUTH, or timeout,
    never a generic "connection failed". Matches connectors/liongard.py's
    LiongardAPIError precedent exactly."""


class SmtpSendError(Exception):
    """The server accepted the connection and (if configured) the
    credentials, but rejected the test message itself -- a genuinely
    different operator problem from SmtpConnectionError. Typically means
    the sending domain isn't verified with the provider (SPF/DKIM); see
    docs/email-setup.md. Kept as its own exception type so
    _test_connection can report it with different, more specific wording
    than a connection failure, per the task's own instruction that this
    distinction is "the most valuable thing this feature can surface."""


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    encryption_mode: str
    verify_cert: bool
    username: str | None
    password: str | None
    from_address: str
    from_name: str


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    """Lenient parse for a config value that may be blank (optional_fields)
    or come from a raw API caller as any of a few honest spellings of
    true/false -- never silently misreads an unrecognized value as the
    unsafe choice: anything that isn't a recognized "false" spelling stays
    at the safe default."""
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("false", "0", "no")


def parse_settings(config: dict, credential: dict) -> SmtpSettings:
    """Validates and normalizes stored config/credential into a typed
    settings object. Raises SmtpConfigError with a specific message for
    anything wrong -- called by both test_connection and email_service.py
    so the two can never drift on what "valid" means.
    """
    host = str(config.get("host") or "").strip()
    if not host:
        raise SmtpConfigError("SMTP host is not set.")

    port_raw = str(config.get("port") or "").strip()
    if not port_raw:
        raise SmtpConfigError("SMTP port is not set.")
    try:
        port = int(port_raw)
    except ValueError as e:
        raise SmtpConfigError(f"SMTP port {port_raw!r} is not a number.") from e
    if not (1 <= port <= 65535):
        raise SmtpConfigError(f"SMTP port {port} is out of range.")

    encryption_mode = str(config.get("encryption_mode") or "").strip().lower()
    if encryption_mode not in _VALID_ENCRYPTION_MODES:
        raise SmtpConfigError(
            f"Encryption mode must be one of {sorted(_VALID_ENCRYPTION_MODES)}, "
            f"got {encryption_mode!r}."
        )

    from_address = str(config.get("from_address") or "").strip()
    if not from_address:
        raise SmtpConfigError("From address is not set.")
    from_name = str(config.get("from_name") or "").strip()

    verify_cert = _parse_bool(config.get("verify_cert"), default=True)

    username = str(credential.get("username") or "").strip() or None
    password = str(credential.get("password") or "").strip() or None

    return SmtpSettings(
        host=host,
        port=port,
        encryption_mode=encryption_mode,
        verify_cert=verify_cert,
        username=username,
        password=password,
        from_address=from_address,
        from_name=from_name,
    )


def _tls_context(verify_cert: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not verify_cert:
        # Explicit, named opt-out for a self-signed internal relay --
        # never the default. Anyone reading a connection dump or this
        # source sees exactly what was disabled and why.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def open_connection(settings: SmtpSettings) -> smtplib.SMTP:
    """Connects, upgrades per encryption_mode, and authenticates if a
    username is configured. Returns a live, ready-to-send SMTP object --
    caller is responsible for .quit()ing it (a `with` block works, since
    smtplib.SMTP is already a context manager).

    Every failure mode raises SmtpConnectionError with a message that
    names which step failed (connect, TLS handshake, auth) rather than a
    single generic "connection failed" -- the same discipline
    connectors/liongard.py's _call() already established for this
    codebase's other connector.
    """
    try:
        if settings.encryption_mode == "tls":
            smtp = smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                timeout=_TIMEOUT_SECONDS,
                context=_tls_context(settings.verify_cert),
            )
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=_TIMEOUT_SECONDS)
    except TimeoutError as e:
        raise SmtpConnectionError(
            f"Timed out connecting to {settings.host}:{settings.port}."
        ) from e
    except (socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise SmtpConnectionError(
            f"Could not connect to {settings.host}:{settings.port} — {e}"
        ) from e

    try:
        smtp.ehlo()
        if settings.encryption_mode == "starttls":
            try:
                smtp.starttls(context=_tls_context(settings.verify_cert))
                smtp.ehlo()
            except smtplib.SMTPNotSupportedError as e:
                raise SmtpConnectionError(
                    f"{settings.host}:{settings.port} does not support STARTTLS."
                ) from e
            except ssl.SSLError as e:
                raise SmtpConnectionError(f"TLS handshake failed: {e}") from e

        if settings.username:
            try:
                smtp.login(settings.username, settings.password or "")
            except smtplib.SMTPAuthenticationError as e:
                raise SmtpConnectionError(
                    f"{settings.host} rejected the credentials (SMTP {e.smtp_code}): "
                    f"{e.smtp_error.decode(errors='replace')}"
                ) from e
            except smtplib.SMTPNotSupportedError as e:
                raise SmtpConnectionError(
                    f"{settings.host}:{settings.port} does not support authentication."
                ) from e
    except Exception as e:
        # Whatever went wrong past this point, the connection is not
        # usable -- close it before propagating so a failed test/send
        # never leaks a socket. Re-raise as SmtpConnectionError uniformly
        # (the inner blocks above already did for the specific cases they
        # recognize; this catches anything else -- e.g. ehlo() itself
        # failing, or a disconnect mid-handshake -- with the same shape
        # rather than leaking a raw smtplib/ssl exception to the caller).
        try:
            smtp.close()
        except Exception:  # noqa: BLE001 -- best-effort cleanup only
            pass
        if isinstance(e, SmtpConnectionError):
            raise
        raise SmtpConnectionError(f"{settings.host} returned an SMTP error: {e}") from e

    return smtp


def _send_test_message(smtp: smtplib.SMTP, settings: SmtpSettings, to_address: str) -> None:
    """Sends one trivially simple message proving delivery -- no
    compliance content (the content rule in email_service.py's own
    docstring is absolute regardless of context, and this path doesn't
    even go through email_service.send()), no tokens, no link that does
    anything. Raises SmtpSendError with the server's own rejection
    reason -- never silently swallowed -- so _test_connection can report
    it distinctly from a connection/auth failure.
    """
    msg = EmailMessage()
    msg["From"] = (
        f"{settings.from_name} <{settings.from_address}>"
        if settings.from_name
        else settings.from_address
    )
    msg["To"] = to_address
    msg["Subject"] = "WinGRC test message"
    msg.set_content(
        "This is a test message from WinGRC confirming your SMTP configuration "
        "can deliver mail. No action is needed."
    )
    try:
        smtp.send_message(msg)
    except smtplib.SMTPSenderRefused as e:
        raise SmtpSendError(
            f"{settings.host} rejected the from-address {settings.from_address!r} "
            f"(SMTP {e.smtp_code}): {e.smtp_error.decode(errors='replace')}. This usually "
            "means the sending domain isn't verified with the provider (SPF/DKIM) -- "
            "see docs/email-setup.md."
        ) from e
    except smtplib.SMTPRecipientsRefused as e:
        raise SmtpSendError(
            f"{settings.host} rejected the recipient {to_address!r}: {e.recipients}"
        ) from e
    except smtplib.SMTPDataError as e:
        raise SmtpSendError(
            f"{settings.host} rejected the message (SMTP {e.smtp_code}): "
            f"{e.smtp_error.decode(errors='replace')}"
        ) from e
    except smtplib.SMTPException as e:
        raise SmtpSendError(f"{settings.host} rejected the test message: {e}") from e


def _test_connection(
    config: dict, credential: dict, test_input: str | None = None
) -> ConnectorTestResult:
    """test_input, if given, is a recipient address to send a real test
    message to -- see this module's own docstring for why. Blank/None
    (the common case, and the only behavior that existed before
    2026-09-14) tests connect + auth only, unchanged."""
    try:
        settings = parse_settings(config, credential)
    except SmtpConfigError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    try:
        smtp = open_connection(settings)
    except SmtpConnectionError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    to_address = (test_input or "").strip()
    if to_address:
        try:
            _send_test_message(smtp, settings, to_address)
        except SmtpSendError as e:
            try:
                smtp.close()
            except Exception:  # noqa: BLE001 -- best-effort cleanup only
                pass
            return ConnectorTestResult(ok=False, message=str(e))

    try:
        smtp.quit()
    except smtplib.SMTPException:
        pass  # connection + auth (+ send, if attempted) already succeeded

    auth_note = f", authenticated as {settings.username}" if settings.username else " (no auth)"
    if to_address:
        # Deliberately not "sent successfully" -- a 250 means the
        # provider ACCEPTED the message for delivery, not that it
        # arrived. Same distinction docs/email-setup.md makes; the UI
        # must not undercut it.
        return ConnectorTestResult(
            ok=True,
            message=(
                f"Connected to {settings.host}:{settings.port} "
                f"({settings.encryption_mode}{auth_note}). Test message accepted by "
                f"{settings.host} for delivery to {to_address} -- check the inbox to "
                "confirm it actually arrived."
            ),
        )
    return ConnectorTestResult(
        ok=True,
        message=(
            f"Connected to {settings.host}:{settings.port} "
            f"({settings.encryption_mode}{auth_note})."
        ),
    )


CONNECTOR = ConnectorSpec(
    key="smtp",
    name="Email (SMTP)",
    # encryption_mode is the field the wl-util-1 incident was actually
    # about: 'tls' reads as "yes, encrypt this" and is wrong for three of
    # SMTP2GO's four published ports. Each option names what it DOES and
    # its typical port(s) rather than just echoing the stored value, and
    # suggests (never overwrites, per ConfigFieldOption.suggests) that
    # port when the field is still blank -- see docs/email-setup.md for
    # the full worked example this was written from.
    config_fields=(
        ConfigField(name="host", label="Host"),
        ConfigField(
            name="port", label="Port", type="number",
            help_text="Free-form -- providers publish alternate ports for networks that "
            "block the standard ones (SMTP2GO's 2525/8025 exist for exactly this). "
            "Pick the port that matches the encryption mode above.",
        ),
        ConfigField(
            name="encryption_mode", label="Encryption Mode", type="select",
            options=(
                ConfigFieldOption(
                    value="starttls",
                    label="STARTTLS — upgrade after connecting (ports 587, 2525, 8025)",
                    suggests={"port": "587"},
                ),
                ConfigFieldOption(
                    value="tls",
                    label="Implicit TLS — encrypted from the first byte (port 465)",
                    suggests={"port": "465"},
                ),
                ConfigFieldOption(value="none", label="None — unencrypted (not recommended)"),
            ),
        ),
        ConfigField(name="from_address", label="From Address"),
        ConfigField(name="from_name", label="From Name", required=False),
        ConfigField(
            name="verify_cert", label="Verify Certificate", type="boolean", required=False,
            help_text="Uncheck only for a self-signed internal relay you control -- it "
            "disables protection against a network path being intercepted.",
        ),
    ),
    credential_fields=("username", "password"),
    hint_field="password",
    test_connection=_test_connection,
    help_text=(
        "Generic SMTP — any provider works, not just one vendor. See docs/email-setup.md "
        "for encryption-mode/port guidance and a worked example. Leave username/password "
        "blank for a relay that accepts unauthenticated mail from this host."
    ),
    kind="notification",
    optional_fields=frozenset({"username", "password"}),
    test_input_label="Send a test message to (optional)",
)
