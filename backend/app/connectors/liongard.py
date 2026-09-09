"""Liongard connector — D.1 (credential entry + test-connection only).

Auth: Liongard's own docs (docs.liongard.com/reference/authentication) —
an Access Key ID + Access Key Secret pair generated for a Liongard user
account, base64(id:secret) sent as the X-ROAR-API-KEY header against
https://{instance}.app.liongard.com/api/v1/. This key is scoped to the
whole MSP instance (see models.py's IntegrationConnection docstring) —
Environments (per-client tenants) live underneath it, they don't each get
their own key. A dedicated Liongard "Reader" role account is sufficient —
WinGRC never needs write access to Liongard, only D.2's future data pull.

Test-connection call: GET /api/v1/environments/count/, the endpoint
Liongard's own docs use to validate a key pair. Uses stdlib urllib (same
convention as auth.py's HIBP k-anonymity check) rather than adding a new
HTTP client dependency.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from . import ConnectorSpec, ConnectorTestResult

_TIMEOUT_SECONDS = 10


def _test_connection(config: dict, credential: dict) -> ConnectorTestResult:
    instance_url = str(config.get("instance_url") or "").strip().rstrip("/")
    access_key_id = str(credential.get("access_key_id") or "")
    access_key_secret = str(credential.get("access_key_secret") or "")

    if not instance_url:
        return ConnectorTestResult(ok=False, message="Instance URL is not set.")
    if not access_key_id or not access_key_secret:
        return ConnectorTestResult(ok=False, message="Access Key ID/Secret are not set.")

    token = base64.b64encode(f"{access_key_id}:{access_key_secret}".encode()).decode()
    url = f"{instance_url}/api/v1/environments/count/"
    req = urllib.request.Request(url, headers={"X-ROAR-API-KEY": token})

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            body = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ConnectorTestResult(
                ok=False,
                message=(
                    f"Liongard rejected the key (HTTP {e.code}) — check that it's active "
                    "and has at least Reader access to this instance."
                ),
            )
        return ConnectorTestResult(ok=False, message=f"Liongard API returned HTTP {e.code}.")
    except urllib.error.URLError as e:
        return ConnectorTestResult(ok=False, message=f"Could not reach {instance_url}: {e.reason}")
    except TimeoutError:
        return ConnectorTestResult(ok=False, message=f"Timed out connecting to {instance_url}.")

    count = body.strip()
    try:
        parsed = json.loads(body)
        if isinstance(parsed, int):
            count = str(parsed)
        elif isinstance(parsed, dict) and "count" in parsed:
            count = str(parsed["count"])
    except (ValueError, TypeError):
        pass  # fall back to the raw body text below

    return ConnectorTestResult(ok=True, message=f"Connected — {count} environment(s) visible.")


CONNECTOR = ConnectorSpec(
    key="liongard",
    name="Liongard",
    config_fields=("instance_url",),
    credential_fields=("access_key_id", "access_key_secret"),
    hint_field="access_key_secret",
    test_connection=_test_connection,
    help_text=(
        "Generate an Access Key ID/Secret for a Liongard user with the Reader role "
        "(Account Settings → Access Tokens) — WinGRC only needs read access. "
        "Instance URL is the subdomain your team uses to sign in, e.g. "
        "https://myinstance.app.liongard.com."
    ),
)
