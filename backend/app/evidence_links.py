"""Archiving and restoring an evidence link: the one path every caller uses.

The product does not delete records. An objective stops resting on an
artifact by its link being archived, and the link says why
(`archive_cause`, migration 0071). The Evidence row and its stored bytes
are never touched here, or anywhere a link is archived.

The objective's ✕ used to delete the link and, on the artifact's last
link, the artifact and its bytes, unaudited. Two exemptions had been carved
out of that, each by the slice that needed to protect its own record (a
published document's, item X; a review cycle's, 2026-10-19). They were the
tell: the rule was never that those two are special.

Causes, and who restores them:
- `deactivation`: a product's deactivation archived it and names the
  product. That product's reactivation restores it (engine.py), and
  nothing else does.
- `detached`: a person detached it. Nothing restores it; it comes back
  live only if a person attaches the same artifact here again.
- `moved_forward`: a list or document link moved to a newer version. The
  newer link is live on the objective.
- `attestation_detach`: `review-cycles-detach-unattested` detached an
  unattested cycle's record.

A detach of a link a deactivation archived clears the product, so that
reactivation does not bring back what a person took off.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_event
from .models import (
    Assessment,
    AssessmentObjective,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
)

DEACTIVATION = "deactivation"
DETACHED = "detached"
MOVED_FORWARD = "moved_forward"
ATTESTATION_DETACH = "attestation_detach"
CAUSES = (DEACTIVATION, DETACHED, MOVED_FORWARD, ATTESTATION_DETACH)


class AlreadyDetached(ValueError):
    """The link is already archived and nothing would restore it."""


def archive(
    link: EvidenceStateLink, *, cause: str, now: datetime,
    product_id: uuid.UUID | None = None, by: str | None = None,
) -> None:
    """The state change of every archive. A link already archived keeps
    the date it was first archived."""
    if cause not in CAUSES:
        raise ValueError(f"unknown archive cause {cause!r}")
    if (product_id is not None) != (cause == DEACTIVATION):
        raise ValueError("only a deactivation names a product")
    if not link.is_archived:
        link.is_archived = True
        link.archived_at = now
    link.archive_cause = cause
    link.archived_by_product = product_id
    if cause in (DETACHED, ATTESTATION_DETACH):
        link.detached_by = by
        link.detached_at = now


def restore(link: EvidenceStateLink) -> None:
    """Live again, with nothing of the archive left on the row (the audit
    log keeps it)."""
    link.is_archived = False
    link.archived_at = None
    link.archive_cause = None
    link.archived_by_product = None
    link.detached_by = None
    link.detached_at = None


def detach(
    session: Session, *, org_id: uuid.UUID, link: EvidenceStateLink, by: str | None,
    cause: str = DETACHED, action: str = "evidence.detach", context: dict | None = None,
    actor: str | None = None, actor_type: str | None = None,
) -> None:
    """This objective stops resting on the artifact. The link is archived,
    not deleted; the artifact and its bytes stay. One audit row, naming
    the artifact, the objective and the assessment. The ✕ and the CLI
    both come here."""
    if link.is_archived and link.archived_by_product is None:
        raise AlreadyDetached("this evidence is already detached from this objective")
    ev = session.get(Evidence, link.evidence_id)
    where = session.execute(
        select(
            Control.control_id, AssessmentObjective.objective_key, Assessment.id, Assessment.name
        )
        .select_from(ControlState)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .join(Assessment, Assessment.id == ControlState.assessment_id)
        .where(ControlState.id == link.control_state_id)
    ).one()
    log_event(
        session, org_id=org_id, action=action, entity_type="evidence", entity_id=ev.id,
        before_value={
            "link_id": str(link.id),
            "control_state_id": str(link.control_state_id),
            "objective_key": f"{where.control_id}[{where.objective_key}]",
            "assessment_id": str(where.id),
            "assessment_name": where.name,
            "was_archived": link.is_archived,
            "archived_by_product": (
                str(link.archived_by_product) if link.archived_by_product else None
            ),
        },
        after_value={"is_archived": True, "archived_by_product": None, "archive_cause": cause},
        context={"evidence_title": ev.title, "detached_by": by, **(context or {})},
        actor=actor, actor_type=actor_type,
    )
    archive(link, cause=cause, now=datetime.now(UTC), by=by)
    session.flush()
