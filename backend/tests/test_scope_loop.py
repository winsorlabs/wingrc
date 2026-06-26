"""Tests for the DB-agnostic scope loop: parse -> reconcile -> render."""

from pathlib import Path

from app.catalog import VIEWS_BY_ID
from app.domain import ChangeType, EntityType
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


def test_render_view_writes_rows(tmp_path):
    ents = parse_workbook(SAMPLE)
    out = render_view(VIEWS_BY_ID["external-services"], ents, tmp_path / "ext.xlsx")
    import openpyxl

    ws = openpyxl.load_workbook(out).active
    names = [r[0] for r in ws.iter_rows(min_row=6, values_only=True) if r[0]]
    assert "Heimdal" in names
    assert "Liongard" in names
