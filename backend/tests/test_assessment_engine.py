"""Tests for the CMMC L2 assessment engine (pure domain functions).

All tests here exercise DB-free logic in assessment.py: compute_sprs() and
magic_loop_updates(). These pass in any environment without a running database.

Design being tested (see assessment.py for full rationale):
- compute_sprs: SPRS = 110 - sum(weight of controls with any unsatisfied obj)
  A control is satisfied only if ALL its objectives are met or inherited.
- magic_loop_updates: for each provider_satisfies or shared baseline_control,
  emit one upsert per objective with status=pending_evidence.
  customer_owns entries emit nothing (no state change, no tasks).
"""

from __future__ import annotations

from app.assessment import (
    SPRS_MAX,
    ControlStatus,
    Responsibility,
    compute_sprs,
    magic_loop_updates,
)

# ---------------------------------------------------------------------------
# compute_sprs: SPRS scoring
# ---------------------------------------------------------------------------


def test_sprs_max_when_all_objectives_met():
    weights = {"AC.L2-3.1.1": 5, "AU.L2-3.3.1": 3}
    objectives = {
        "AC.L2-3.1.1": ["obj-ac-a", "obj-ac-b"],
        "AU.L2-3.3.1": ["obj-au-a"],
    }
    statuses = {
        "obj-ac-a": ControlStatus.MET,
        "obj-ac-b": ControlStatus.MET,
        "obj-au-a": ControlStatus.MET,
    }
    assert compute_sprs(weights, objectives, statuses) == SPRS_MAX


def test_sprs_deducts_full_weight_for_any_unmet_objective():
    # AU has one not_met objective → full weight of 3 deducted
    weights = {"AC.L2-3.1.1": 5, "AU.L2-3.3.1": 3}
    objectives = {
        "AC.L2-3.1.1": ["obj-ac-a", "obj-ac-b"],
        "AU.L2-3.3.1": ["obj-au-a"],
    }
    statuses = {
        "obj-ac-a": ControlStatus.MET,
        "obj-ac-b": ControlStatus.MET,
        "obj-au-a": ControlStatus.NOT_MET,
    }
    assert compute_sprs(weights, objectives, statuses) == 107


def test_sprs_deducts_weight_when_one_of_many_objectives_not_met():
    # AC has 3 objectives; only one is not_met → full control weight deducted
    weights = {"AC.L2-3.1.1": 5}
    objectives = {"AC.L2-3.1.1": ["obj-a", "obj-b", "obj-c"]}
    statuses = {
        "obj-a": ControlStatus.MET,
        "obj-b": ControlStatus.MET,
        "obj-c": ControlStatus.NOT_MET,
    }
    assert compute_sprs(weights, objectives, statuses) == 105


def test_sprs_pending_evidence_counts_as_unmet():
    # pending_evidence is NOT met for SPRS purposes
    weights = {"AU.L2-3.3.1": 3}
    objectives = {"AU.L2-3.3.1": ["obj-a"]}
    statuses = {"obj-a": ControlStatus.PENDING_EVIDENCE}
    assert compute_sprs(weights, objectives, statuses) == 107


def test_sprs_partial_counts_as_unmet():
    weights = {"AU.L2-3.3.1": 3}
    objectives = {"AU.L2-3.3.1": ["obj-a"]}
    statuses = {"obj-a": ControlStatus.PARTIAL}
    assert compute_sprs(weights, objectives, statuses) == 107


def test_sprs_inherited_counts_as_met():
    # inherited = satisfied by an external authorized system
    weights = {"SC.L2-3.13.15": 1}
    objectives = {"SC.L2-3.13.15": ["obj-a"]}
    statuses = {"obj-a": ControlStatus.INHERITED}
    assert compute_sprs(weights, objectives, statuses) == 110


def test_sprs_not_applicable_counts_as_unmet():
    # not_applicable still deducts — the assessor must justify scoping out
    weights = {"MA.L2-3.7.1": 3}
    objectives = {"MA.L2-3.7.1": ["obj-a"]}
    statuses = {"obj-a": ControlStatus.NOT_APPLICABLE}
    assert compute_sprs(weights, objectives, statuses) == 107


def test_sprs_control_with_no_objectives_is_skipped():
    # A control with no objectives in the lookup contributes 0 deduction
    weights = {"AC.L2-3.1.1": 5}
    objectives: dict[str, list[str]] = {}  # no objectives registered
    statuses: dict[str, str] = {}
    assert compute_sprs(weights, objectives, statuses) == SPRS_MAX


def test_sprs_unknown_objective_defaults_to_not_met():
    # An objective_id present in objectives_by_control but absent from
    # objective_statuses defaults to not_met
    weights = {"AU.L2-3.3.1": 3}
    objectives = {"AU.L2-3.3.1": ["obj-missing"]}
    statuses: dict[str, str] = {}  # obj-missing not here
    assert compute_sprs(weights, objectives, statuses) == 107


def test_sprs_all_not_met_gives_minimum():
    # NIST 800-171 has 110 practices; if all 3 test controls are unmet
    weights = {"AC": 5, "AU": 3, "IA": 1}
    objectives = {"AC": ["a"], "AU": ["b"], "IA": ["c"]}
    statuses = {
        "a": ControlStatus.NOT_MET,
        "b": ControlStatus.NOT_MET,
        "c": ControlStatus.NOT_MET,
    }
    assert compute_sprs(weights, objectives, statuses) == 110 - 5 - 3 - 1


def test_sprs_mixed_met_unmet():
    weights = {"AC.L2-3.1.1": 5, "AU.L2-3.3.1": 3, "IA.L2-3.5.1": 1}
    objectives = {
        "AC.L2-3.1.1": ["ac-a", "ac-b"],
        "AU.L2-3.3.1": ["au-a"],
        "IA.L2-3.5.1": ["ia-a"],
    }
    statuses = {
        "ac-a": ControlStatus.MET,
        "ac-b": ControlStatus.MET,
        "au-a": ControlStatus.NOT_MET,
        "ia-a": ControlStatus.MET,
    }
    # Only AU deducts → 110 - 3 = 107
    assert compute_sprs(weights, objectives, statuses) == 107


# ---------------------------------------------------------------------------
# magic_loop_updates: control_state pre-population
# ---------------------------------------------------------------------------


def test_magic_loop_provider_satisfies_emits_updates():
    entries = [
        {
            "control_id": "AU.L2-3.3.1",
            "objectives": ["a", "b", "c"],
            "classification": "provider_satisfies",
        }
    ]
    lookup = {
        ("AU.L2-3.3.1", "a"): "obj-au-a",
        ("AU.L2-3.3.1", "b"): "obj-au-b",
        ("AU.L2-3.3.1", "c"): "obj-au-c",
    }
    updates = magic_loop_updates(entries, lookup)

    assert len(updates) == 3
    for u in updates:
        assert u["status"] == ControlStatus.PENDING_EVIDENCE
        assert u["responsibility"] == Responsibility.PROVIDER_SATISFIES


def test_magic_loop_shared_emits_shared_responsibility():
    entries = [
        {
            "control_id": "AC.L2-3.1.1",
            "objectives": ["a", "b"],
            "classification": "shared",
        }
    ]
    lookup = {
        ("AC.L2-3.1.1", "a"): "obj-ac-a",
        ("AC.L2-3.1.1", "b"): "obj-ac-b",
    }
    updates = magic_loop_updates(entries, lookup)

    assert len(updates) == 2
    assert all(u["responsibility"] == Responsibility.SHARED for u in updates)
    assert all(u["status"] == ControlStatus.PENDING_EVIDENCE for u in updates)


def test_magic_loop_customer_owns_emits_nothing():
    entries = [
        {
            "control_id": "IA.L2-3.5.1",
            "objectives": ["a", "b"],
            "classification": "customer_owns",
        }
    ]
    lookup = {
        ("IA.L2-3.5.1", "a"): "obj-ia-a",
        ("IA.L2-3.5.1", "b"): "obj-ia-b",
    }
    updates = magic_loop_updates(entries, lookup)
    assert updates == []


def test_magic_loop_objective_not_in_lookup_is_skipped():
    entries = [
        {
            "control_id": "AC.L2-3.1.1",
            "objectives": ["a", "z"],  # "z" not in lookup
            "classification": "provider_satisfies",
        }
    ]
    lookup = {("AC.L2-3.1.1", "a"): "obj-ac-a"}
    updates = magic_loop_updates(entries, lookup)

    assert len(updates) == 1
    assert updates[0]["objective_id"] == "obj-ac-a"


def test_magic_loop_mixed_classifications():
    # Three entries: one provider_satisfies, one shared, one customer_owns
    entries = [
        {
            "control_id": "AU.L2-3.3.1",
            "objectives": ["a"],
            "classification": "provider_satisfies",
        },
        {
            "control_id": "AC.L2-3.1.1",
            "objectives": ["a"],
            "classification": "shared",
        },
        {
            "control_id": "IA.L2-3.5.1",
            "objectives": ["a"],
            "classification": "customer_owns",
        },
    ]
    lookup = {
        ("AU.L2-3.3.1", "a"): "obj-au",
        ("AC.L2-3.1.1", "a"): "obj-ac",
        ("IA.L2-3.5.1", "a"): "obj-ia",
    }
    updates = magic_loop_updates(entries, lookup)

    obj_ids = {u["objective_id"] for u in updates}
    assert "obj-au" in obj_ids
    assert "obj-ac" in obj_ids
    assert "obj-ia" not in obj_ids
    assert len(updates) == 2


def test_magic_loop_empty_objectives_list():
    # An entry with no objectives (e.g. batch entry pending catalog link)
    entries = [
        {
            "control_id": "AU.L2-3.3.1",
            "objectives": [],
            "classification": "provider_satisfies",
        }
    ]
    lookup = {("AU.L2-3.3.1", "a"): "obj-au-a"}
    updates = magic_loop_updates(entries, lookup)
    assert updates == []


def test_magic_loop_empty_entries():
    updates = magic_loop_updates([], {})
    assert updates == []


def test_magic_loop_result_contains_required_keys():
    entries = [
        {
            "control_id": "AU.L2-3.3.1",
            "objectives": ["a"],
            "classification": "provider_satisfies",
        }
    ]
    lookup = {("AU.L2-3.3.1", "a"): "obj-au-a"}
    updates = magic_loop_updates(entries, lookup)

    assert len(updates) == 1
    u = updates[0]
    assert "objective_id" in u
    assert "status" in u
    assert "responsibility" in u
    assert u["objective_id"] == "obj-au-a"


# ---------------------------------------------------------------------------
# StrEnum values: ensure DB constraint strings stay in sync
# ---------------------------------------------------------------------------


def test_control_status_values():
    assert ControlStatus.NOT_MET == "not_met"
    assert ControlStatus.PENDING_EVIDENCE == "pending_evidence"
    assert ControlStatus.PARTIAL == "partial"
    assert ControlStatus.MET == "met"
    assert ControlStatus.NOT_APPLICABLE == "not_applicable"
    assert ControlStatus.INHERITED == "inherited"


def test_responsibility_values():
    assert Responsibility.PROVIDER_SATISFIES == "provider_satisfies"
    assert Responsibility.SHARED == "shared"
    assert Responsibility.CUSTOMER_OWNS == "customer_owns"
    assert Responsibility.EXTERNAL_SYSTEM == "external_system"
