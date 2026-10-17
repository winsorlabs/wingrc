"""Is the certificate this deployment serves about to expire?

The July certificate for dev.wingrc.us lapsed on 2026-10-14 and the first
anyone knew was a browser warning: renewal is manual by decision (DNS-01
by hand, no DNS credential on the host -- deploy/tls/renew.sh), so the
only thing that can say "it is time" is a check. This is that check, run
by the scheduler (`tls_expiry_check`) and shown to msp_admin as a banner.

**It reads what nginx serves, not the files.** A renewed file nginx never
re-read is the same outage, so the job opens a TLS connection to the
proxy with each public hostname as SNI and reads the certificate it is
handed. It reads it only: verification is off because the question is
"when does this expire", not "do we trust it" -- an expired certificate
must still be readable to be reported.

**The verdict is derived, never stored** (the same rule as a document's
overdue state, document_reviews.py): the job records what it read and
when; `status()` decides at read time, so the window and the clock are
applied when someone looks. **A check that has not run is itself a
finding**: no reading, or one older than STALE_AFTER, is reported as
needing attention, because a silent check is how this was missed.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from cryptography import x509

WINDOW_DAYS = 30
# The job runs every 6 hours; two days without a reading means it is not
# running, whatever the last reading said.
STALE_AFTER = timedelta(hours=48)


def targets(*urls: str | None) -> list[str]:
    """The hostnames to check: each https URL's host, in order, once.
    A deployment with no https public URL serves no certificate of ours."""
    out: list[str] = []
    for url in urls:
        parsed = urlparse((url or "").strip())
        if parsed.scheme == "https" and parsed.hostname and parsed.hostname not in out:
            out.append(parsed.hostname)
    return out


@dataclass(frozen=True)
class Reading:
    name: str
    not_after: datetime | None
    error: str | None


def read_served(name: str, connect_host: str, port: int = 443, timeout: float = 5.0) -> Reading:
    """The certificate served for `name` (as SNI) at connect_host:port."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((connect_host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=name) as tls,
        ):
            der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        return Reading(name=name, not_after=None, error=f"{type(e).__name__}: {e}")
    if not der:
        return Reading(name=name, not_after=None, error="no certificate presented")
    return Reading(
        name=name, not_after=x509.load_der_x509_certificate(der).not_valid_after_utc, error=None
    )


def to_result(readings: list[Reading]) -> dict:
    """What the job stores in job_run.result."""
    return {
        "certs": [
            {
                "name": r.name,
                "not_after": r.not_after.isoformat() if r.not_after else None,
                "error": r.error,
            }
            for r in readings
        ]
    }


def _verdict(not_after: datetime | None, now: datetime) -> str:
    if not_after is None:
        return "unreadable"
    if not_after <= now:
        return "expired"
    if not_after - now <= timedelta(days=WINDOW_DAYS):
        return "expiring"
    return "ok"


def status(
    *, configured: bool, result: dict | None, checked_at: datetime | None, now: datetime
) -> dict:
    """The banner's answer. `attention` is true when anyone should act:
    a certificate inside its window, expired or unreadable, or no recent
    reading at all."""
    if not configured:
        return {"configured": False, "attention": False, "checked_at": None,
                "stale": False, "certs": []}
    stale = checked_at is None or now - checked_at > STALE_AFTER
    certs = []
    for c in (result or {}).get("certs", []):
        not_after = datetime.fromisoformat(c["not_after"]) if c.get("not_after") else None
        certs.append({
            "name": c["name"],
            "not_after": not_after,
            "days_left": (not_after - now).days if not_after else None,
            "verdict": _verdict(not_after, now),
            "error": c.get("error"),
        })
    attention = stale or not certs or any(c["verdict"] != "ok" for c in certs)
    return {"configured": True, "attention": attention, "checked_at": checked_at,
            "stale": stale, "certs": certs}


def configured_targets(settings) -> list[str]:
    return targets(settings.public_url, settings.storage_public_endpoint)


def check_now(settings) -> dict:
    """The scheduler job's body. When it ran is the job_run's own
    finished_at, not a second clock in the result."""
    return to_result([
        read_served(n, settings.tls_check_connect_host, settings.tls_check_connect_port)
        for n in configured_targets(settings)
    ])
