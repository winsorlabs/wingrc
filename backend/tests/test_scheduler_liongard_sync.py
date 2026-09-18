"""Integration tests for scheduler.py's liongard_daily_sync job (D.3
second half) -- the per-org loop, the hard "never writes scope_entity"
constraint, digest notification (one email per org per recipient, not
one per device), and the "no contact holds the role" case handled
explicitly rather than silently dropped.

connectors/liongard.py is monkeypatched at the module level, same as
test_liongard_sync_api.py / test_liongard_sync_approval_api.py.
email_service.send() is monkeypatched, same as
test_scheduler_review_cycles.py's own reasoning: what's under test here
is scheduler.py's own logic, not SMTP transport.

Run in-container:
    docker compose exec backend pytest tests/test_scheduler_liongard_sync.py -m integration -v
"""

from __future__ import annotations

import uuid

import pytest

from app import scheduler
from app.config import get_settings
from app.connectors import liongard as liongard_module
from app.email_service import EmailSendResult
from app.models import (
    Contact,
    ContactDocumentationRole,
    LiongardSyncNotification,
    LiongardSyncResult,
    Organization,
    OrgLiongardEnvironment,
    ScopeEntity,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sent_emails(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def _fake_send(session, *, to, subject, body, template):
        calls.append((to, subject, body))
        return EmailSendResult(sent=True)

    monkeypatch.setattr(scheduler.email_service, "send", _fake_send)
    return calls


def _device_row(hostname: str = "SBX-Mini-01") -> dict:
    return {
        "ID": f"id-{hostname}", "EnvironmentID": 8815, "InventoryState": "Inventory",
        "Hostname": hostname, "SerialNumber": f"SN-{hostname}",
        "MACAddress": [], "Manufacturer": "Apple", "Model": "Macmini8,1",
        "OperatingSystem": "macOS", "Type": "desktop", "AssetTagNumber": None,
    }


@pytest.fixture(autouse=True)
def _stub_liongard(monkeypatch):
    state = {"devices": [_device_row()], "identities": []}
    monkeypatch.setattr(
        liongard_module, "pull_device_profiles",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["devices"], total_count=len(state["devices"])
        ),
    )
    monkeypatch.setattr(
        liongard_module, "pull_identities",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=state["identities"], total_count=len(state["identities"])
        ),
    )
    return state


def _seed_org_with_mapping(
    db_session, *, env_id: int = 8815, name: str | None = None
) -> Organization:
    org = Organization(name=name or f"LiongardJobOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    from app.crypto import encrypt_credential
    from app.models import IntegrationConnection

    conn = db_session.query(IntegrationConnection).filter_by(connector_key="liongard").first()
    if conn is None:
        ciphertext, key_version = encrypt_credential(
            '{"access_key_id": "AKID", "access_key_secret": "secret"}'
        )
        conn = IntegrationConnection(
            connector_key="liongard",
            config={"instance_url": "https://myinstance.app.liongard.com"},
            encrypted_credential=ciphertext,
            credential_key_version=key_version,
        )
        db_session.add(conn)
        db_session.flush()
    db_session.add(
        OrgLiongardEnvironment(
            org_id=org.id, liongard_environment_id=env_id, liongard_environment_name="Acme Corp"
        )
    )
    db_session.flush()
    return org


def _add_contact(db_session, org_id, *, role: str, email: str) -> Contact:
    c = Contact(org_id=org_id, name=f"Contact {role}", email=email, affiliation="customer")
    db_session.add(c)
    db_session.flush()
    db_session.add(ContactDocumentationRole(contact_id=c.id, role=role))
    db_session.flush()
    return c


def test_syncs_every_org_with_a_mapping(db_session, sent_emails):
    org_a = _seed_org_with_mapping(db_session, env_id=8815, name="OrgA")
    org_b = _seed_org_with_mapping(db_session, env_id=9999, name="OrgB")
    result = scheduler._liongard_daily_sync(db_session)
    assert result["orgs_synced"] == 2

    results = db_session.query(LiongardSyncResult).all()
    org_ids = {r.org_id for r in results}
    assert org_ids == {org_a.id, org_b.id}


def test_never_writes_scope_entity(db_session, sent_emails):
    _seed_org_with_mapping(db_session)
    scheduler._liongard_daily_sync(db_session)
    assert db_session.query(ScopeEntity).count() == 0


def test_notifies_security_officer_and_it_admin_once_each(db_session, sent_emails):
    org = _seed_org_with_mapping(db_session)
    _add_contact(db_session, org.id, role="security_officer", email="so@example.com")
    _add_contact(db_session, org.id, role="it_admin", email="it@example.com")

    result = scheduler._liongard_daily_sync(db_session)
    assert result["orgs_with_new"] == 1
    assert result["notifications_sent"] == 2
    recipients = {to for to, _subj, _body in sent_emails}
    assert recipients == {"so@example.com", "it@example.com"}

    notifications = db_session.query(LiongardSyncNotification).filter_by(org_id=org.id).all()
    assert len(notifications) == 2
    assert all(n.notified_at is not None for n in notifications)


def test_digest_email_names_no_org_no_count_no_device_detail(db_session, sent_emails):
    org = _seed_org_with_mapping(db_session)
    _add_contact(db_session, org.id, role="security_officer", email="so@example.com")
    scheduler._liongard_daily_sync(db_session)

    assert len(sent_emails) == 1
    _to, subject, body = sent_emails[0]
    assert org.name not in subject
    assert org.name not in body
    assert "SBX-Mini-01" not in body
    assert "1" not in body  # no count of any kind


def test_no_contact_holding_role_is_reported_not_silently_dropped(db_session, sent_emails):
    org = _seed_org_with_mapping(db_session)
    result = scheduler._liongard_daily_sync(db_session)
    assert result["orgs_with_no_contact"] == 1
    assert sent_emails == []

    sync_result = db_session.query(LiongardSyncResult).filter_by(org_id=org.id).one()
    assert any("No contact holds" in w for w in (sync_result.warnings or []))


def test_org_with_no_mapping_is_never_synced(db_session, sent_emails):
    org = Organization(name=f"UnmappedOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    result = scheduler._liongard_daily_sync(db_session)
    assert result["orgs_synced"] == 0
    assert db_session.query(LiongardSyncResult).filter_by(org_id=org.id).count() == 0


def test_one_org_failing_does_not_stop_the_rest(db_session, sent_emails, monkeypatch):
    org_a = _seed_org_with_mapping(db_session, env_id=8815, name="OrgA-fails")
    org_b = _seed_org_with_mapping(db_session, env_id=9999, name="OrgB-ok")

    real_pull = liongard_module.pull_device_profiles

    def _flaky(config, credential, environment_id):
        if environment_id == 8815:
            raise liongard_module.LiongardAPIError("Liongard rejected the key (HTTP 401)")
        return real_pull(config, credential, environment_id)

    monkeypatch.setattr(liongard_module, "pull_device_profiles", _flaky)

    result = scheduler._liongard_daily_sync(db_session)
    assert result["errors"] == 1
    assert result["orgs_synced"] == 1
    assert db_session.query(LiongardSyncResult).filter_by(org_id=org_b.id).count() == 1
    assert db_session.query(LiongardSyncResult).filter_by(org_id=org_a.id).count() == 0


def test_second_daily_sync_supersedes_first_pending_result(db_session, sent_emails):
    org = _seed_org_with_mapping(db_session)
    scheduler._liongard_daily_sync(db_session)
    first = db_session.query(LiongardSyncResult).filter_by(org_id=org.id).one()

    scheduler._liongard_daily_sync(db_session)
    db_session.expire_all()
    assert first.status == "superseded"
    results = db_session.query(LiongardSyncResult).filter_by(org_id=org.id).all()
    assert len(results) == 2
    assert sum(1 for r in results if r.status == "pending_review") == 1
