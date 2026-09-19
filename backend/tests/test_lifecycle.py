"""One tenant, the whole lifecycle, one narrative test.

This is a consolidation pass, not a feature slice. The last two weeks
added baseline versioning, multi-tool coverage, AI vendor-doc research,
the asset approval workflow, and a persisted sync result -- each verified
on its own, in its own feature-scoped test file. Nothing until now has
walked a tenant through more than one of them in sequence. Twice already
in that stretch, the real bug was an OLDER path routing around a NEWER
guard (seed_baselines never deleting an unmatched BaselineControl; a
fresh Liongard pull's default status=active silently promoting a
pending_approval device through the pre-existing workbook-apply path).
Both were found by asking "what else writes this?", not by any test --
this file is what closes that gap.

**Clean-start choice, stated explicitly (asked for by the task): a fresh
isolated bench-stack database via pytest's own db_session fixture
(migrated, empty), not `wingrc reset-dev`.** reset-dev resets an
already-populated dev database back to its seeded baseline (keeps the
"Acme MSP" org, wipes assessment-layer data) -- it's the right tool for
resetting a shared dev box, not for a from-scratch integration test that
needs to construct its own tenant and assert against a real NIST 800-171
catalog. seed_catalog()/seed_baselines() are called directly here, the
same functions `wingrc seed-catalog`/`wingrc seed-baselines` call.

**HTTP throughout, not direct engine calls, on purpose.** Several of
this pass's own watch-items are specifically about the request layer:
RLS under a long multi-step session, and whether every mutation actually
funnels through the guard that's supposed to gate it. A test that calls
engine.py functions directly under the owner role never exercises RLS at
all -- see conftest.py's own db_session/_app_session docstrings for why
those are two genuinely different privilege levels. Every action below
goes through TestClient + `_app_session` (wingrc_app role, RLS enforced)
+ `_authed` (the real per-role identity for that step), exactly the path
a real deployment's request handling takes.

**Real data where it exists, synthetic where it doesn't.** RocketCyber
is `baselines/rocketcyber.yaml`, loaded via the real `seed_baselines()` --
the actual product, the actual controls, the actual classifications.
There is no committed `datto-rmm.yaml` in this repo (checked, not
assumed) -- "Datto RMM" here is a hand-built second product overlapping
RocketCyber on AU.L2-3.3.1 (RocketCyber=provider_satisfies, Datto RMM=
shared), the same overlap shape test_multi_tool_coverage.py already
established as the real motivating case for that feature.

**Four controls, four distinct concerns, deliberately not overlapping:**
  AU.L2-3.3.1   -- the multi-tool overlap (both products contribute).
  AC.L2-3.1.11  -- evidence collected and marked met (the SPRS/bundle
                   point-in-time anchor).
  AC.L2-3.1.1   -- classification changed in the reimport (shared ->
                   provider_satisfies): this IS detected as "changed" by
                   move_org_product_version.
  AC.L2-3.1.2   -- provider_contribution text edited, classification and
                   objectives left alone: this is NOT detected as
                   "changed" by move_org_product_version (see the
                   surprise recorded in the walk itself, marked SURPRISE
                   below) -- its contributor keeps pointing at the OLD
                   version's row even after the tenant moves versions.
  MA.L2-3.7.1   -- dropped entirely from the reimport (§1's old bug,
                   still worth a live regression check here even though
                   test_admin_products.py already covers it in isolation).

Run in-container (slow by design -- explicitly acceptable per the task):
    docker compose exec backend pytest tests/test_lifecycle.py -m integration -v -s
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.connectors import liongard as liongard_module
from app.crypto import encrypt_credential
from app.db import get_session
from app.domain import CanonicalEntity, EntityStatus, EntityType, Source
from app.main import app
from app.models import (
    AssetApproval,
    BaselineControl,
    Control,
    ControlStateContributor,
    ControlStateHistory,
    EvidenceStateLink,
    Framework,
    IntegrationConnection,
    Organization,
    OrgLiongardEnvironment,
    OrgProduct,
    Product,
    ProductBaselineVersion,
    ScopeEntity,
    SprsSnapshot,
)
from app.repo import upsert as repo_upsert
from app.seeds.baselines import seed_baselines
from app.seeds.catalog import seed_catalog
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration

_STAMP_RE = re.compile(r'<div class="stamp">.*?</div>')


def _strip_stamp(html: bytes) -> str:
    """Every bundle page carries a `generated {timestamp}` stamp -- see
    bundle_service.py:_stamp. Two bundles of otherwise-identical content
    are never byte-identical because of it; strip it before comparing
    "in substance", per the task's own wording."""
    return _STAMP_RE.sub("", html.decode())


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return f"http://fake/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)

    def get_bytes(self, key: str) -> bytes:
        return self.files.get(key, b"")


def _device_row(hostname: str, *, environment_id: int = 8815) -> dict:
    return {
        "ID": f"id-{hostname}", "EnvironmentID": environment_id, "InventoryState": "Inventory",
        "Hostname": hostname, "SerialNumber": f"SN-{hostname}",
        "MACAddress": [], "Manufacturer": "Dell", "Model": "Latitude",
        "OperatingSystem": "Windows 11", "Type": "laptop", "AssetTagNumber": None,
    }


def _identity_row(email: str, *, environment_id: int = 8815) -> dict:
    return {
        "ID": f"id-{email}", "EnvironmentID": environment_id, "InventoryState": "Inventory",
        "Email": email, "Username": email, "Type": "user", "Enabled": True,
        "DisplayName": email.split("@")[0],
    }


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


@pytest.fixture
def catalog(db_session):
    """Real NIST 800-171 Rev 2 catalog (110 controls, real SPRS weights)
    plus the real rocketcyber.yaml baseline -- the same two functions
    `wingrc seed-catalog`/`wingrc seed-baselines` call."""
    catalog_result = seed_catalog(db_session)
    baselines_result = seed_baselines(db_session)
    db_session.commit()
    assert catalog_result["controls"] == 110, (
        "CLAUDE.md's own verified control count -- if this changes, the "
        "catalog itself changed and every hardcoded SPRS assumption below "
        "needs re-deriving, not silently trusting a new number."
    )
    assert baselines_result["missing_controls"] == [], (
        "rocketcyber.yaml references a control id the freshly-seeded catalog "
        "doesn't have -- catalog/baseline drift, not this test's own bug"
    )
    return catalog_result


def _get(db_session, model, **filters):
    stmt = select(model)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    row = db_session.scalars(stmt).first()
    assert row is not None, f"{model.__name__} not found for {filters}"
    return row


@pytest.fixture
def client(db_session, storage):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_full_tenant_lifecycle(client: TestClient, db_session, storage, catalog):
    def _as(user) -> None:
        """Swap the authenticated identity for the rest of the test -- the
        same TestClient instance is reused; dependency_overrides is looked
        up fresh per request, so this is enough to change "who" every
        following call acts as."""
        app.dependency_overrides[get_current_user] = _authed(db_session, user)

    msp_admin = _make_fake_user(role="msp_admin", display_name="MSP Engineer")
    customer_poc = _make_fake_user(role="customer_poc", display_name="Client POC")
    c3pao = _make_fake_user(role="c3pao_assessor", display_name="Assessor")

    fw = _get(db_session, Framework, key="nist-800-171-r2")
    rocketcyber = _get(db_session, Product, key="rocketcyber")

    # ------------------------------------------------------------------ #
    # Step 1 — Onboard the org through the real endpoints, start an       #
    # assessment.                                                         #
    # ------------------------------------------------------------------ #
    org = Organization(name=f"LifecycleOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, msp_admin, org_id=org.id)
    _grant(db_session, customer_poc, org_id=org.id)
    _grant(db_session, c3pao, org_id=org.id)
    _as(msp_admin)

    r = client.patch(
        f"/orgs/{org.id}/profile",
        json={
            "cage_code": "1A2B3", "uei": "ABCDEFGH1234",
            "city": "Norfolk", "state_or_province": "VA",
        },
    )
    assert r.status_code == 200, r.text

    r = client.put(
        f"/orgs/{org.id}/system-description",
        json={
            "system_name": "Lifecycle Test System",
            "system_type": "on_premise",
            "operational_status": "operational",
            "cui_categories": ["Controlled Technical Information"],
        },
    )
    assert r.status_code == 200, r.text

    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Sam Security", "email": "sam@lifecycle.example", "affiliation": "customer"},
    )
    assert r.status_code == 201, r.text
    so_contact_id = r.json()["id"]
    r = client.post(
        f"/orgs/{org.id}/contacts/{so_contact_id}/roles", json={"role": "security_officer"}
    )
    assert r.status_code == 201, r.text

    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Ivy IT", "email": "ivy@lifecycle.example", "affiliation": "msp"},
    )
    assert r.status_code == 201, r.text
    it_contact_id = r.json()["id"]
    r = client.post(f"/orgs/{org.id}/contacts/{it_contact_id}/roles", json={"role": "it_admin"})
    assert r.status_code == 201, r.text

    r = client.post(
        f"/orgs/{org.id}/assessments",
        json={"framework_id": str(fw.id), "name": "Lifecycle Assessment"},
    )
    assert r.status_code == 201, r.text
    assessment_id = r.json()["id"]

    # ------------------------------------------------------------------ #
    # Step 2 — Activate two genuinely overlapping products.               #
    # ------------------------------------------------------------------ #
    rocketcyber.is_published = True
    db_session.flush()

    datto = Product(
        framework_id=fw.id, key="datto-rmm", name="Datto RMM", provider="Kaseya",
        category="RMM", asset_type="SPA", role="RMM agent deployment and patching.",
        is_published=True,
    )
    db_session.add(datto)
    db_session.flush()
    datto_v1 = ProductBaselineVersion(product_id=datto.id, version_number=1)
    db_session.add(datto_v1)
    db_session.flush()
    datto.current_version_id = datto_v1.id
    db_session.flush()

    au_331 = _get(db_session, Control, control_id="AU.L2-3.3.1", framework_id=fw.id)
    cm_341 = _get(db_session, Control, control_id="CM.L2-3.4.1", framework_id=fw.id)
    db_session.add_all([
        BaselineControl(
            product_id=datto.id, baseline_version_id=datto_v1.id, control_id=au_331.id,
            objectives=["a", "b", "c", "d", "e", "f"], classification="shared",
            candidate_state="pending_evidence",
            provider_contribution="RMM-deployed agents feed event data into the SOC stack.",
        ),
        BaselineControl(
            product_id=datto.id, baseline_version_id=datto_v1.id, control_id=cm_341.id,
            objectives=["a", "b"], classification="provider_satisfies",
            candidate_state="pending_evidence",
            provider_contribution="RMM enforces the approved software/configuration baseline.",
        ),
    ])
    db_session.commit()

    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment_id}/products/{rocketcyber.id}/activate", json={}
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment_id}/products/{datto.id}/activate", json={}
    )
    assert r.status_code == 200, r.text

    states = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/control-states").json()
    by_key = {(s["control_id"], s["objective_key"]): s for s in states}
    au_a = by_key[("AU.L2-3.3.1", "a")]
    assert {c["product_key"] for c in au_a["contributors"]} == {"rocketcyber", "datto-rmm"}, (
        "the whole point of this step -- two products, one shared control"
    )
    assert au_a["responsibility"] == "shared", (
        "mixed provider_satisfies + shared contributors resolve to the weakest "
        "(most customer-inclusive) claim -- test_mixed_contributors_resolve_to_shared_"
        "weakest_wins's own invariant, now proven end to end through real activation, "
        "not a unit-level call"
    )
    assessments = client.get(f"/orgs/{org.id}/assessments").json()
    score_after_two_products = next(
        a for a in assessments if a["id"] == assessment_id
    )["sprs_score"]

    # ------------------------------------------------------------------ #
    # Step 3 — Liongard sync: run it, review, approve one, reject one.    #
    # ------------------------------------------------------------------ #
    ciphertext, key_version = encrypt_credential(
        '{"access_key_id": "AKID", "access_key_secret": "s3cr3t"}'
    )
    db_session.add(IntegrationConnection(
        connector_key="liongard",
        config={"instance_url": "https://myinstance.app.liongard.com"},
        encrypted_credential=ciphertext, credential_key_version=key_version,
    ))
    db_session.add(OrgLiongardEnvironment(
        org_id=org.id, liongard_environment_id=8815, liongard_environment_name="Lifecycle Env"
    ))
    db_session.commit()

    mp = pytest.MonkeyPatch()
    mp.setattr(
        liongard_module, "pull_device_profiles",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=[_device_row("LT-APPROVE"), _device_row("LT-REJECT")], total_count=2,
        ),
    )
    mp.setattr(
        liongard_module, "pull_identities",
        lambda config, credential, environment_id: liongard_module.InventoryPull(
            records=[], total_count=0
        ),
    )
    try:
        r = client.post(f"/orgs/{org.id}/liongard-sync-results/sync-now")
        assert r.status_code == 201, r.text
        sync_result_id = r.json()["id"]
        assert r.json()["status"] == "pending_review"

        detail = client.get(f"/orgs/{org.id}/liongard-sync-results/{sync_result_id}").json()
        changes_by_key = {c["natural_key"]: c for c in detail["changes"]}
        assert changes_by_key["SN-LT-APPROVE"]["incoming"]["status"] == "pending_approval"
        approve_change_id = changes_by_key["SN-LT-APPROVE"]["id"]
        reject_change_id = changes_by_key["SN-LT-REJECT"]["id"]

        r = client.post(
            f"/orgs/{org.id}/liongard-sync-results/{sync_result_id}"
            f"/changes/{approve_change_id}/approve",
            json={"checklist_confirmations": {}},
        )
        assert r.status_code == 200, r.text
        approved_entity_id = r.json()["scope_entity_id"]

        r = client.post(
            f"/orgs/{org.id}/liongard-sync-results/{sync_result_id}"
            f"/changes/{reject_change_id}/reject",
            json={"reason": "Personal device, not authorized for CUI."},
        )
        assert r.status_code == 200, r.text
        rejected_entity_id = r.json()["scope_entity_id"]
    finally:
        mp.undo()

    db_session.expire_all()
    approved_entity = db_session.get(ScopeEntity, uuid.UUID(approved_entity_id))
    assert approved_entity.status == "active" and approved_entity.in_boundary is True
    rejected_entity = db_session.get(ScopeEntity, uuid.UUID(rejected_entity_id))
    assert rejected_entity.status == "active" and rejected_entity.in_boundary is False, (
        "rejection asserts out-of-boundary, never decommissioned -- the device "
        "exists on the network either way"
    )

    # SURPRISE candidate, checked directly: the ordinary manual scope PATCH
    # has no awareness of pending_approval at all. Confirmed live here, not
    # just by reading the code -- see the report for why this is left as a
    # finding, not fixed in this pass (touches who is authorized to admit
    # an asset into the boundary; the correct shape of a fix is a product
    # decision, not an obvious one-liner).
    bypass_row = repo_upsert(
        db_session, org.id,
        CanonicalEntity(
            entity_type=EntityType.DEVICE, natural_key="SN-LT-BYPASS-CHECK",
            status=EntityStatus.PENDING_APPROVAL, source=Source.LIONGARD,
        ),
    )
    db_session.commit()
    r = client.patch(f"/orgs/{org.id}/scope/{bypass_row.id}", json={"status": "active"})
    assert r.status_code == 200, (
        "FINDING: the ordinary scope PATCH endpoint has no guard against "
        "promoting a pending_approval entity to active outside the approval "
        "workflow -- no AssetApproval row, no checklist, no approval-specific "
        "audit action. If this ever starts 422ing, the finding was fixed; "
        "update this test to assert the refusal instead of documenting the gap."
    )
    assert db_session.scalars(
        select(AssetApproval).where(AssetApproval.scope_entity_id == bypass_row.id)
    ).first() is None, "confirms: promoted with no acceptance record at all"

    # ------------------------------------------------------------------ #
    # Step 4 — Collect evidence until AC.L2-3.1.11 (both objectives)      #
    # reaches met. Also collect for the AU.L2-3.3.1 overlap control --    #
    # this is what step 10's evidence-archival check needs real evidence  #
    # attached to.                                                        #
    # ------------------------------------------------------------------ #
    tasks = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/evidence-tasks").json()
    session_timeout_task = next(t for t in tasks if "Session timeout" in t["title"])
    au_task = next(t for t in tasks if "Defined event types" in t["title"])

    for task in (session_timeout_task, au_task):
        r = client.post(
            f"/orgs/{org.id}/assessments/{assessment_id}/evidence-tasks/{task['id']}/collect",
            files={"file": ("evidence.txt", b"screenshot placeholder", "text/plain")},
            data={"artifact_type": "screenshot", "title": "Collected evidence"},
        )
        assert r.status_code == 201, r.text

    for ref in session_timeout_task["linked_states"]:
        r = client.patch(
            f"/orgs/{org.id}/assessments/{assessment_id}/control-states/{ref['control_state_id']}",
            json={"status": "met"},
        )
        assert r.status_code == 200, r.text

    states = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/control-states").json()
    ac_1111 = [s for s in states if s["control_id"] == "AC.L2-3.1.11"]
    assert all(s["status"] == "met" for s in ac_1111), "the control this walk brings to met"
    assert all(s["evidence_count"] >= 1 for s in ac_1111)

    au_cs_id = next(
        s["id"] for s in states
        if s["control_id"] == "AU.L2-3.3.1" and s["objective_key"] == "a"
    )

    # ------------------------------------------------------------------ #
    # Step 5 — SPRS snapshot: record the score.                          #
    # ------------------------------------------------------------------ #
    score_after_met = client.get(f"/orgs/{org.id}/assessments").json()
    score_after_met = next(a for a in score_after_met if a["id"] == assessment_id)["sprs_score"]
    assert score_after_met > score_after_two_products, (
        "marking a real control met must raise the score, or nothing above did what it claimed"
    )

    # ------------------------------------------------------------------ #
    # Step 6 — Export a bundle. Keep it.                                 #
    # ------------------------------------------------------------------ #
    r = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/bundle")
    assert r.status_code == 200, r.text
    zf_v1 = zipfile.ZipFile(io.BytesIO(r.content))
    impl_name = next(n for n in zf_v1.namelist() if n.endswith("ssp/02_implementation.html"))
    scoring_name = next(n for n in zf_v1.namelist() if n.endswith("summary/scoring.html"))
    impl_html_v1 = _strip_stamp(zf_v1.read(impl_name))
    scoring_html_v1 = _strip_stamp(zf_v1.read(scoring_name))
    assert "shared" in impl_html_v1.lower() or "provider" in impl_html_v1.lower()

    # ------------------------------------------------------------------ #
    # Step 7 — Import a changed baseline for RocketCyber: classification  #
    # change (AC.L2-3.1.1), provider_contribution edit (AC.L2-3.1.2), a   #
    # dropped control (MA.L2-3.7.1). AU.L2-3.3.1 (the overlap) is left    #
    # untouched on purpose.                                               #
    # ------------------------------------------------------------------ #
    with open("baselines/rocketcyber.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    original_provider_contribution_312 = None
    new_controls = []
    for entry in raw["controls"]:
        ctrl = entry.get("control")
        ctrl_ids = [ctrl] if isinstance(ctrl, str) else ctrl
        if ctrl_ids == ["AC.L2-3.1.1"]:
            entry = {**entry, "classification": "provider_satisfies"}
        elif ctrl_ids == ["AC.L2-3.1.2"]:
            original_provider_contribution_312 = entry["provider_contribution"]
            entry = {
                **entry,
                "provider_contribution": original_provider_contribution_312 + " (Edited.)",
            }
        elif ctrl_ids == ["MA.L2-3.7.1"]:
            continue  # dropped
        new_controls.append(entry)
    raw["controls"] = new_controls
    assert original_provider_contribution_312 is not None

    yaml_bytes = yaml.safe_dump(raw).encode()
    r = client.post(
        "/admin/products/import/apply",
        files={"file": ("rocketcyber-v2.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 201, r.text
    apply_result = r.json()
    assert apply_result["version_created"] is True
    assert apply_result["version_number"] == 2
    assert apply_result["removed_controls"] == ["MA.L2-3.7.1"], (
        "§1's old bug, checked live end to end here too: a dropped control "
        "must be visible in the apply result, not silently kept"
    )

    db_session.expire_all()
    db_session.refresh(rocketcyber)
    r = client.get(f"/admin/products/{rocketcyber.id}/versions")
    versions = {v["version_number"]: v["id"] for v in r.json()}
    assert set(versions) == {1, 2}
    v2_id = versions[2]

    org_product_row = _get(db_session, OrgProduct, org_id=org.id, product_id=rocketcyber.id)
    assert str(org_product_row.baseline_version_id) != v2_id, (
        "the tenant must stay pinned to version 1 -- an import must never move an "
        "already-activated tenant on its own"
    )

    # ------------------------------------------------------------------ #
    # Step 8 — Re-export the bundle. Must be unchanged in substance.      #
    # This is the single most important assertion in the whole pass.     #
    # ------------------------------------------------------------------ #
    r = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/bundle")
    assert r.status_code == 200, r.text
    zf_v2 = zipfile.ZipFile(io.BytesIO(r.content))
    impl_html_v2 = _strip_stamp(zf_v2.read(impl_name))
    scoring_html_v2 = _strip_stamp(zf_v2.read(scoring_name))

    assert impl_html_v2 == impl_html_v1, (
        "a baseline reimport must never change what an already-generated bundle "
        "renders for a tenant still pinned to the old version"
    )
    assert scoring_html_v2 == scoring_html_v1, (
        "SPRS-visible content must be unaffected by the reimport"
    )

    score_after_reimport = client.get(f"/orgs/{org.id}/assessments").json()
    score_after_reimport = next(
        a for a in score_after_reimport if a["id"] == assessment_id
    )["sprs_score"]
    assert score_after_reimport == score_after_met, (
        "importing a new baseline version must never move an already-activated "
        "tenant's SPRS score by itself"
    )

    # ------------------------------------------------------------------ #
    # Step 9 — Move the tenant to the new version. AC.L2-3.1.1 (real      #
    # classification change) and MA.L2-3.7.1 (dropped) must go to        #
    # needs_review. AC.L2-3.1.2 (text-only edit) must NOT -- and its      #
    # contributor must still point at the OLD version's row. This is the #
    # SURPRISE this walk exists to catch: move_org_product_version's own  #
    # diff is classification/objective-key only, never provider_          #
    # contribution/note/scope_note text -- a pure text edit is invisible  #
    # to it by design (see that function's own docstring), which also    #
    # means it stays invisible after a version move, not just before one.#
    # No current bundle renders that text to a tenant, so there is no     #
    # live consequence today -- but it is a real, load-bearing fact about #
    # what "moved to the new version" actually means, worth having        #
    # written down before it surprises someone.                          #
    # ------------------------------------------------------------------ #
    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment_id}/products/{rocketcyber.id}/move-version",
        json={"target_version_id": v2_id},
    )
    assert r.status_code == 200, r.text
    move_result = r.json()
    # Both counters are per-OBJECTIVE, matching _run_loop's own
    # objectives_updated granularity (see move_org_product_version's own
    # docstring) -- MA.L2-3.7.1 has one objective ([a]); AC.L2-3.1.1 has
    # six ([a]-[f]).
    assert move_result["controls_lost"] == 1
    assert move_result["controls_changed"] == 6

    states = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/control-states").json()
    by_ctrl = {}
    for s in states:
        by_ctrl.setdefault(s["control_id"], []).append(s)

    assert all(s["status"] == "needs_review" for s in by_ctrl["AC.L2-3.1.1"])
    assert all(s["status"] == "needs_review" for s in by_ctrl["MA.L2-3.7.1"])
    assert all(s["status"] == "not_met" for s in by_ctrl["AC.L2-3.1.2"]), (
        "unchanged by the move -- its classification never changed, only "
        "descriptive text move_org_product_version doesn't compare"
    )
    ac_312_cs_id = by_ctrl["AC.L2-3.1.2"][0]["id"]
    contributor_312 = db_session.scalars(
        select(ControlStateContributor).where(
            ControlStateContributor.control_state_id == uuid.UUID(ac_312_cs_id)
        )
    ).first()
    contributor_bc_312 = db_session.get(BaselineControl, contributor_312.baseline_control_id)
    assert contributor_bc_312.baseline_version_id != uuid.UUID(v2_id), (
        "SURPRISE, confirmed live: AC.L2-3.1.2's contributor still points at "
        "version 1's row even after the tenant moved to version 2 -- the "
        "provider_contribution edit is real in the library but invisible to "
        "this tenant's own contributor pointer, permanently, unless something "
        "else about that control's classification ever changes too"
    )
    assert contributor_bc_312.provider_contribution == original_provider_contribution_312

    # Evidence survives a version move -- unlike deactivation.
    ac_1111_after_move = [s for s in states if s["control_id"] == "AC.L2-3.1.11"]
    assert all(s["status"] == "met" for s in ac_1111_after_move), (
        "the move only touches objectives whose OWN claim changed -- AC.L2-3.1.11 "
        "wasn't part of this reimport at all and must be untouched"
    )

    # ------------------------------------------------------------------ #
    # Step 10 — Deactivate Datto RMM (one of the two overlapping          #
    # products). AU.L2-3.3.1 falls to needs_review; RocketCyber is still  #
    # recorded as a contributor; which tool left is visible in the        #
    # change_reason. Also: does deactivation's known all-or-nothing       #
    # evidence-archival bite the RocketCyber-collected evidence on this   #
    # shared control?                                                     #
    # ------------------------------------------------------------------ #
    au_link_before = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.control_state_id == uuid.UUID(au_cs_id))
    ).all()
    assert len(au_link_before) >= 1 and all(not lnk.is_archived for lnk in au_link_before)

    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment_id}/products/{datto.id}/deactivate", json={}
    )
    assert r.status_code == 200, r.text

    states = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/control-states").json()
    au_a_after = next(
        s for s in states
        if s["control_id"] == "AU.L2-3.3.1" and s["objective_key"] == "a"
    )
    assert au_a_after["status"] == "needs_review", (
        "losing a contributing tool is exactly when a coverage claim deserves "
        "a second look, even though RocketCyber still covers it"
    )
    assert {c["product_key"] for c in au_a_after["contributors"]} == {"rocketcyber"}, (
        "the remaining contributor is still recorded"
    )
    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == uuid.UUID(au_cs_id)
        )
    ).all()
    assert any("Datto RMM" in (h.change_reason or "") for h in history), (
        "which tool went away must be visible in the record, by name"
    )

    db_session.expire_all()
    au_link_after = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.control_state_id == uuid.UUID(au_cs_id))
    ).all()
    # FINDING, confirmed live here (not just read from the code), report-
    # only: deactivate_org_product's evidence-link archival is scoped per
    # control_state, not per product -- flagged during the multi-tool
    # slice, never fixed. This asserts the CURRENTLY OBSERVED (buggy)
    # behavior on purpose: RocketCyber's own collected evidence on
    # AU.L2-3.3.1 gets archived even though RocketCyber -- not Datto RMM,
    # the product actually being deactivated -- is the one still covering
    # it. When this is fixed, this assertion will start failing; flip it
    # to `is False` at that point, don't just delete it (see CLAUDE.md's
    # "don't delete tests on behavior change" convention).
    assert all(lnk.is_archived for lnk in au_link_after), (
        "if this fails, the evidence-archival granularity bug was fixed -- "
        "update this assertion to `not lnk.is_archived`, and update the "
        "roadmap finding that documents it"
    )

    # ------------------------------------------------------------------ #
    # Step 11 — Periodic review cycle, attested by an authenticated       #
    # customer_poc.                                                       #
    # ------------------------------------------------------------------ #
    _as(msp_admin)
    r = client.post(f"/orgs/{org.id}/review-cycles")
    assert r.status_code == 201, r.text
    cycle_id = r.json()["id"]

    _as(customer_poc)
    r = client.post(
        f"/orgs/{org.id}/review-cycles/{cycle_id}/attest", json={"comment": "Confirmed."}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "attested"
    assert r.json()["user_id"] == str(customer_poc.id)

    # ------------------------------------------------------------------ #
    # Step 12 — Complete the assessment. Record an SPRS submission.       #
    # ------------------------------------------------------------------ #
    _as(msp_admin)
    r = client.post(f"/orgs/{org.id}/assessments/{assessment_id}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"

    r = client.post(
        f"/orgs/{org.id}/sprs-submissions",
        json={
            "score": score_after_met, "submitted_date": date.today().isoformat(),
            "submitted_by_contact_id": so_contact_id, "assessment_id": assessment_id,
        },
    )
    assert r.status_code == 201, r.text

    # ------------------------------------------------------------------ #
    # Step 13 — Re-export the bundle once more. Compare against steps 6/8 #
    # (implementation text for untouched controls must still match) and   #
    # assert the things that SHOULD have changed actually did.            #
    # ------------------------------------------------------------------ #
    r = client.get(f"/orgs/{org.id}/assessments/{assessment_id}/bundle")
    assert r.status_code == 200, r.text
    zf_v3 = zipfile.ZipFile(io.BytesIO(r.content))
    cover_name = next(n for n in zf_v3.namelist() if n.endswith("cover.html"))
    cover_html_v3 = _strip_stamp(zf_v3.read(cover_name))
    assert (
        "submitted" in cover_html_v3.lower()
        or "closed" in cover_html_v3.lower()
        or str(score_after_met) in cover_html_v3
    )

    # ------------------------------------------------------------------ #
    # Separately — c3pao_assessor walks the read paths. Every mutation    #
    # attempted must be refused; every GET must still work.               #
    # ------------------------------------------------------------------ #
    _as(c3pao)

    assert client.get(
        f"/orgs/{org.id}/assessments/{assessment_id}/control-states"
    ).status_code == 200
    assert client.get(f"/orgs/{org.id}/assessments/{assessment_id}/bundle").status_code == 200
    assert client.get(f"/orgs/{org.id}/liongard-sync-results").status_code == 200
    assert client.get(f"/orgs/{org.id}/review-cycles").status_code == 200

    cs_id = states[0]["id"]
    assert client.patch(
        f"/orgs/{org.id}/assessments/{assessment_id}/control-states/{cs_id}", json={"status": "met"}
    ).status_code == 403
    assert client.post(
        f"/orgs/{org.id}/assessments/{assessment_id}/products/{rocketcyber.id}/activate", json={}
    ).status_code == 403
    assert client.post(f"/orgs/{org.id}/liongard-sync-results/sync-now").status_code == 403
    assert client.post(f"/orgs/{org.id}/review-cycles").status_code == 403
    assert client.post(f"/orgs/{org.id}/assessments/{assessment_id}/complete").status_code == 403

    # ------------------------------------------------------------------ #
    # Final cross-cutting checks: RLS held for this whole multi-step,     #
    # multi-identity session (a second org, never granted, must be        #
    # invisible throughout), and no unexpected SPRS drift anywhere.       #
    # ------------------------------------------------------------------ #
    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.commit()
    _as(msp_admin)  # msp_admin has no membership in other_org
    assert client.get(f"/orgs/{other_org.id}/assessments").status_code == 403

    snapshots = db_session.scalars(
        select(SprsSnapshot)
        .where(SprsSnapshot.assessment_id == uuid.UUID(assessment_id))
        .order_by(SprsSnapshot.seq)
    ).all()
    assert len(snapshots) >= 5, "one per recompute_sprs call site this walk actually exercised"
    assert snapshots[-1].score == score_after_met, (
        "no step after marking AC.L2-3.1.11 met should have changed the score again -- "
        "reimport, version move, deactivation (needs_review, not met, doesn't re-deduct "
        "since the control was already failing SPRS before), review cycle, and completion "
        "are all score-neutral for this specific set of steps"
    )
