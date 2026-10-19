"""An objective's evidence: nothing detached is destroyed, nothing archived
reads as current, and "Replaced by" is said only when a person said it
(2026-10-19, evidence_links.py).

Every archive here goes through the real paths -- the HTTP detach, and
engine.deactivate_org_product / activate_org_product -- never a hand-set
flag, so each is the case it claims to be.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import evidence_links
from app.engine import activate_org_product
from app.models import (
    Assessment,
    BaselineControl,
    ControlState,
    Evidence,
    EvidenceStateLink,
    OrgProduct,
    Product,
    ProductBaselineVersion,
)
from app.routers.dashboard import _blocked_objectives
from tests.test_bundle import (  # noqa: F401 -- fixtures
    _bundle_url,
    _seed,
    client,
    storage,
)


def _base(d) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"


def _cs_url(d, cs) -> str:
    return f"{_base(d)}/control-states/{cs.id}/evidence"


def _product(db_session, d, name: str, objectives=("b",)) -> Product:
    p = Product(
        framework_id=d["fw"].id, key=f"prod-{uuid.uuid4().hex[:6]}", name=name,
        provider="Vendor", category="ESP", asset_type="SPA", role="test", is_published=True,
    )
    db_session.add(p)
    db_session.flush()
    v = ProductBaselineVersion(product_id=p.id, version_number=1)
    db_session.add(v)
    db_session.flush()
    p.current_version_id = v.id
    db_session.add(BaselineControl(
        product_id=p.id, baseline_version_id=v.id, control_id=d["ctrl"].id,
        objectives=list(objectives), classification="shared", candidate_state="pending_evidence",
    ))
    db_session.add(OrgProduct(org_id=d["org"].id, product_id=p.id, status="candidate"))
    db_session.flush()
    activate_org_product(db_session, d["org"].id, p.id, d["assessment"].id)
    db_session.flush()
    return p


def _file(db_session, storage, d, cs, title: str) -> Evidence:  # noqa: F811
    ev_id = uuid.uuid4()
    key = f"{d['org'].id}/evidence/{ev_id}/{ev_id}.pdf"
    data = f"%PDF-1.4 {title}".encode()
    storage.upload_file(key, data, "application/pdf")
    ev = Evidence(
        id=ev_id, org_id=d["org"].id, kind="file", title=title, artifact_type="export",
        storage_key=key, mime_type="application/pdf", file_size_bytes=len(data),
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
    db_session.flush()
    return ev


def _link(db_session, ev) -> EvidenceStateLink:
    db_session.expire_all()
    return db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev.id)
    ).one()


def _states(db_session, d):
    db_session.expire_all()
    rows = db_session.execute(
        select(ControlState.id, ControlState.status)
        .where(ControlState.assessment_id == d["assessment"].id)
    ).all()
    return sorted((str(i), s) for i, s in rows), db_session.scalar(
        select(Assessment.sprs_score).where(Assessment.id == d["assessment"].id)
    )


def _manifest(client, d) -> str:  # noqa: F811
    r = client.get(_bundle_url(d))
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return zf.read(next(n for n in zf.namelist() if n.endswith("manifest.html"))).decode()


@pytest.mark.integration
def test_reactivation_restores_only_what_its_deactivation_archived(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    """The trap: deactivation archives with the product and reactivation
    restores by it. A person's detach must never come back -- neither one
    made while the link was live, nor one made after a deactivation had
    already archived it."""
    d = _seed(db_session, storage, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    heimdal = _product(db_session, d, "Heimdal")
    kept = _file(db_session, storage, d, d["cs_b"], "Kept by the tool")
    early = _file(db_session, storage, d, d["cs_b"], "Detached while live")
    late = _file(db_session, storage, d, d["cs_b"], "Detached after the deactivation")
    db_session.commit()

    assert client.post(f"{_cs_url(d, d['cs_b'])}/{early.id}/detach").status_code == 204
    r = client.post(f"{_base(d)}/products/{heimdal.id}/deactivate", json={})
    assert r.status_code == 200, r.text
    by = (_link(db_session, kept).archived_by_product, _link(db_session, late).archived_by_product)
    assert by == (heimdal.id, heimdal.id), (
        "fixture: the deactivation archives both live links with its product"
    )
    assert _link(db_session, early).archived_by_product is None, (
        "fixture: a link already detached is not the deactivation's to archive"
    )

    # "Detach for good": a deactivation's archive, detached by a person.
    assert client.post(f"{_cs_url(d, d['cs_b'])}/{late.id}/detach").status_code == 204
    late_link = _link(db_session, late)
    assert (late_link.archive_cause, late_link.archived_by_product) == ("detached", None)

    assert client.post(f"{_base(d)}/products/{heimdal.id}/activate", json={}).status_code == 200
    restored = _link(db_session, kept)
    assert (restored.is_archived, restored.archive_cause, restored.archived_by_product) == (
        False, None, None
    ), "reactivation restores what its deactivation archived"
    for ev in (early, late):
        lk = _link(db_session, ev)
        assert (lk.is_archived, lk.archive_cause) == (True, "detached"), (
            f"{ev.title}: a person's detach survives reactivation"
        )
    live = client.get(_cs_url(d, d["cs_b"])).json()
    assert [i["title"] for i in live] == ["Kept by the tool"]


@pytest.mark.integration
def test_replaced_by_is_said_only_when_a_person_named_it(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    """Defender covers objective b throughout, so a replacement would be
    easy to infer for both products leaving it. Only the one deactivated
    naming Defender says so."""
    d = _seed(db_session, storage, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    defender = _product(db_session, d, "Defender")
    heimdal = _product(db_session, d, "Heimdal")
    sentinel = _product(db_session, d, "Sentinel One")
    from_heimdal = _file(db_session, storage, d, d["cs_b"], "Heimdal console export")
    db_session.commit()

    r = client.post(
        f"{_base(d)}/products/{heimdal.id}/deactivate",
        json={"replaced_by_product_id": str(defender.id)},
    )
    assert r.status_code == 200, r.text
    from_sentinel = _file(db_session, storage, d, d["cs_b"], "Sentinel One export")
    db_session.commit()
    assert client.post(f"{_base(d)}/products/{sentinel.id}/deactivate").status_code == 200

    archived = client.get(f"{_cs_url(d, d['cs_b'])}/archived").json()
    by_title = {i["title"]: i for i in archived["items"]}
    named, unnamed = by_title["Heimdal console export"], by_title["Sentinel One export"]
    assert (named["product_name"], named["replaced_by_name"], named["deactivated_by"]) == (
        "Heimdal", "Defender", "Test Admin"
    )
    assert named["replaced_by_covers"] is True
    assert unnamed["product_name"] == "Sentinel One"
    assert (unnamed["replaced_by_name"], unnamed["replaced_by_covers"]) == (None, None), (
        "nothing inferred, though Defender covers this objective"
    )
    assert archived["covered_by"] == ["Defender"]

    op = db_session.scalars(
        select(OrgProduct).where(OrgProduct.product_id == heimdal.id)
    ).one()
    assert (op.replaced_by_product_id, op.deactivated_by) == (defender.id, "Test Admin")

    html = _manifest(client, d).split("<h3>Links archived</h3>", 1)[1]
    assert (
        "Heimdal was deactivated and replaced by Defender, as stated by Test Admin at deactivation"
        in html
    ), "the statement, with who made it, survives into the bundle"
    assert "Sentinel One was deactivated, with nothing named to replace it" in html
    assert html.count("replaced by") == 1
    for ev in (from_heimdal, from_sentinel):
        assert db_session.get(Evidence, ev.id) is not None


@pytest.mark.integration
def test_a_replacement_is_refused_only_when_it_is_not_a_product_or_is_itself(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    """It need not cover the objective: the person states a fact about their
    estate, and the mapping disagreeing is no reason to refuse it."""
    d = _seed(db_session, storage, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    heimdal = _product(db_session, d, "Heimdal")
    elsewhere = _product(db_session, d, "Covers only a", objectives=("a",))
    db_session.commit()
    url = f"{_base(d)}/products/{heimdal.id}/deactivate"
    assert client.post(url, json={"replaced_by_product_id": str(heimdal.id)}).status_code == 422
    assert client.post(url, json={"replaced_by_product_id": str(uuid.uuid4())}).status_code == 422
    assert client.post(url, json={"replaced_by_product_id": str(elsewhere.id)}).status_code == 200


@pytest.mark.integration
def test_a_persons_detach_reaches_the_bundle_with_who_and_when(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    d = _seed(db_session, storage, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    ev = d["evidence"]
    _manifest(client, d)  # an export recomputes SPRS; compare from one
    before = _states(db_session, d)
    assert client.post(f"{_cs_url(d, d['cs_a'])}/{ev.id}/detach").status_code == 204
    link = _link(db_session, ev)
    html = _manifest(client, d)
    retired = html.split("<h3>Links archived</h3>", 1)[1]
    assert ev.title in retired
    assert f"detached by Test Admin on {link.detached_at.astimezone(UTC):%Y-%m-%d}" in retired
    assert storage.get_bytes(d["ev_key"]) == d["ev_bytes"], "the bytes, read back"
    after_bundle = _states(db_session, d)
    assert after_bundle[0] == before[0], "detaching moves no status"
    assert after_bundle[1] == before[1], "nor SPRS"


@pytest.mark.integration
def test_counts_and_widgets_read_live_links_only(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    """Four readers counted every link, archived included. Latent while
    only a deactivation archived generic evidence; live once the
    unattested detach ran (2026-10-19), and certain once the ✕ archived."""
    d = _seed(db_session, storage, org_id=fake_msp_admin.org_id, fake_msp_admin=fake_msp_admin)
    cs = d["cs_a"]
    cs.status = "pending_evidence"
    db_session.commit()
    states = client.get(f"{_base(d)}/control-states").json()
    assert next(s for s in states if s["id"] == str(cs.id))["evidence_count"] == 1, (
        "fixture: the seeded evidence is live on [a]"
    )
    assert str(cs.id) not in {str(b.control_state_id) for b in _blocked_objectives(
        db_session, d["assessment"].id)[0]}, "fixture: [a] has evidence, so is not blocked"

    assert client.post(f"{_cs_url(d, cs)}/{d['evidence'].id}/detach").status_code == 204

    states = client.get(f"{_base(d)}/control-states").json()
    assert next(s for s in states if s["id"] == str(cs.id))["evidence_count"] == 0
    assert client.get(_cs_url(d, cs)).json() == []
    manifest = client.get(f"{_base(d)}/evidence-manifest").json()
    assert next(
        o for o in manifest["objectives"] if o["control_state_id"] == str(cs.id)
    )["evidence"] == []
    db_session.expire_all()
    assert str(cs.id) in {str(b.control_state_id) for b in _blocked_objectives(
        db_session, d["assessment"].id)[0]}, "archived evidence is not evidence present"


def test_every_archive_says_why_and_only_a_deactivation_names_a_product():
    """The database holds the rule, not only this module."""
    lk = EvidenceStateLink(evidence_id=uuid.uuid4(), control_state_id=uuid.uuid4())
    with pytest.raises(ValueError):
        evidence_links.archive(lk, cause="detached", now=datetime.now(UTC), product_id=uuid.uuid4())
    with pytest.raises(ValueError):
        evidence_links.archive(lk, cause="deactivation", now=datetime.now(UTC))
    with pytest.raises(ValueError):
        evidence_links.archive(lk, cause="removed", now=datetime.now(UTC))
    names = {c.name for c in EvidenceStateLink.__table__.constraints}
    assert {
        "ck_evidence_state_link_archive_cause",
        "ck_evidence_state_link_archived_has_cause",
        "ck_evidence_state_link_product_is_deactivation",
    } <= names
