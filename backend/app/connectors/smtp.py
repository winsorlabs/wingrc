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

test_connection() does NOT send a message. Two reasons: the generic
`TestConnectionFn` signature (`config, credential) -> ConnectorTestResult`)
has no room for a "send to whom" parameter without changing every other
connector's interface and the Administration screen just for this one
case, and sending a real email on every click of "Test connection" (an
admin will click it repeatedly while tuning host/port/encryption) would
surprise whoever's on the receiving end. Connect + EHLO + the TLS
handshake for the selected mode + AUTH (if configured) already catches
the overwhelming majority of real misconfigurations — wrong host/port,
wrong encryption mode for the port, wrong credentials. What it can't
catch (SPF/DKIM alignment, whether the receiving provider actually
accepts the message) is exercised for real the first time an invite or
password-reset email actually sends, whose outcome the admin already
sees in that response.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from dataclasses import dataclass

from . import ConnectorSpec, ConnectorTestResult

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


def _test_connection(config: dict, credential: dict) -> ConnectorTestResult:
    try:
        settings = parse_settings(config, credential)
    except SmtpConfigError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    try:
        smtp = open_connection(settings)
    except SmtpConnectionError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    try:
        smtp.quit()
    except smtplib.SMTPException:
        pass  # connection + auth already succeeded; a failure saying goodbye isn't a test failure

    auth_note = f", authenticated as {settings.username}" if settings.username else " (no auth)"
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
    config_fields=("host", "port", "encryption_mode", "from_address", "from_name", "verify_cert"),
    credential_fields=("username", "password"),
    hint_field="password",
    test_connection=_test_connection,
    help_text=(
        "Generic SMTP — any provider works, not just one vendor. Encryption mode: "
        "'starttls' (plaintext connect, then upgrade — typically port 587 or a "
        "provider's alternate like 2525), 'tls' (implicit TLS from the first byte — "
        "typically port 465 or an alternate like 8025), or 'none' (unencrypted, for a "
        "trusted internal relay only). Leave username/password blank for a relay that "
        "accepts unauthenticated mail from this host. Uncheck 'Verify certificate' only "
        "for a self-signed internal relay you control — it disables protection against "
        "a network path being intercepted."
    ),
    kind="notification",
    optional_fields=frozenset({"username", "password", "verify_cert"}),
)
