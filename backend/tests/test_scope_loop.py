"""Tests for the DB-agnostic scope loop: parse -> reconcile -> render."""

from pathlib import Path

from app.catalog import VIEWS_BY_ID
from app.domain import CanonicalEntity, ChangeType, EntityType
from app.importers.workbook import parse_workbook
from app.reconcile import reconcile
from app.render import render_view

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "authorized-entities.example.xlsx"


def test_parse_sample_counts():
    ents = parse_workbook(SAMPLE)
    by_type = {t: 0 for t in (e.entity_type for e in ents)}
    for e in ents:
        by_type[e.entity_type] += 1
    assert by_type[EntityType.PERSON] == 3
    assert by_type[EntityType.DEVICE] == 3
    assert by_type[EntityType.EXTERNAL_SERVICE] == 5
    assert by_type[EntityType.PROCESS] == 2


def test_category_inference():
    ents = parse_workbook(SAMPLE)
    devices = [e for e in ents if e.entity_type == EntityType.DEVICE]
    cats = {e.scope_category.value for e in devices if e.scope_category}
    assert "CUI Asset" in cats
    assert "SPA" in cats


def test_reconcile_detects_new_and_missing():
    base = parse_workbook(SAMPLE)
    incoming = base[:-1]  # drop one -> should be MISSING
    result = reconcile(base, incoming)
    assert result.summary()["missing"] == 1
    assert all(c.change_type != ChangeType.NEW for c in result.changes)


def test_reconcile_ignores_mac_address_reordering():
    """The single most likely source of noise in the review diff once an
    attribute is list-shaped: two imports of the same underlying device,
    where the source (or Liongard) happens to return per-NIC data in a
    different order, must reconcile as UNCHANGED -- not report a spurious
    CHANGED purely from element order.
    """
    current = [
        CanonicalEntity(
            entity_type=EntityType.DEVICE,
            natural_key="ASSET-0001",
            attributes={"mac_addresses": ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]},
        )
    ]
    incoming = [
        CanonicalEntity(
            entity_type=EntityType.DEVICE,
            natural_key="ASSET-0001",
            attributes={"mac_addresses": ["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:01"]},
        )
    ]

    result = reconcile(current, incoming)

    assert len(result.changes) == 1
    assert result.changes[0].change_type == ChangeType.UNCHANGED
    assert result.changes[0].field_diffs == {}


def test_reconcile_still_detects_real_mac_address_changes():
    current = [
        CanonicalEntity(
            entity_type=EntityType.DEVICE,
            natural_key="ASSET-0001",
            attributes={"mac_addresses": ["aa:bb:cc:dd:ee:01"]},
        )
    ]
    incoming = [
        CanonicalEntity(
            entity_type=EntityType.DEVICE,
            natural_key="ASSET-0001",
            attributes={"mac_addresses": ["aa:bb:cc:dd:ee:99"]},
        )
    ]

    result = reconcile(current, incoming)

    assert result.changes[0].change_type == ChangeType.CHANGED
    assert "mac_addresses" in result.changes[0].field_diffs


def test_render_view_writes_rows(tmp_path):
    ents = parse_workbook(SAMPLE)
    out = render_view(VIEWS_BY_ID["external-services"], ents, tmp_path / "ext.xlsx")
    import openpyxl

    ws = openpyxl.load_workbook(out).active
    names = [r[0] for r in ws.iter_rows(min_row=6, values_only=True) if r[0]]
    assert "Heimdal" in names
    assert "Liongard" in names
