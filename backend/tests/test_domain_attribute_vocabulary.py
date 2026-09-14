"""Guards against domain.py:DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES and
routers/scope.py:DeviceSoftwareAttributes drifting into two different
descriptions of the same canonical vocabulary (2026-09-18). The Pydantic
model's fields are hand-declared (each needs its own type/validator, which
can't be generated from a plain frozenset) rather than derived from the
constant, so this test is what keeps them in sync instead -- see both
docstrings for the full reasoning.
"""

from __future__ import annotations

from app.domain import DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES, DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES
from app.routers.scope import DeviceSoftwareAttributes


def test_pydantic_model_fields_match_the_canonical_constant():
    assert set(DeviceSoftwareAttributes.model_fields) == DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES


def test_comparable_attributes_is_canonical_minus_last_login_user():
    assert DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES == DEVICE_SOFTWARE_CANONICAL_ATTRIBUTES - {
        "last_login_user"
    }
    assert "last_login_user" not in DEVICE_SOFTWARE_COMPARABLE_ATTRIBUTES
