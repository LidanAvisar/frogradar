"""Move stored radar profiles to the current check rule (profile.CHECK_RULE) without LLM calls.

    python -m app.pipeline.radar.recheck            (make radar-recheck)

Rule 2 (S15): statements the verifier marked partial are kept and labelled "Partly verified";
only unsupported ones stay dropped; a profile is rejected only when "what it is" is not kept.
The verifier's verdicts and notes are stored with every dropped statement, so no model is asked
again. The ranking relation already counted partial statements, so scores don't change.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RadarCandidate
from app.pipeline.radar.profile import CHECK_RULE, apply_check_rule

logger = logging.getLogger(__name__)


def recheck_profiles(session: Session) -> list[dict[str, object]]:
    out = []
    for c in session.scalars(
        select(RadarCandidate)
        .where(RadarCandidate.merged_into_id.is_(None), RadarCandidate.profile.is_not(None))
        .order_by(RadarCandidate.score.desc().nulls_last(), RadarCandidate.id)
    ):
        if (c.profile or {}).get("check_rule") == CHECK_RULE:
            continue
        before = str(c.profile_status)
        c.profile, status, moved = apply_check_rule(c.profile or {})
        c.profile_status = status
        out.append(
            {"id": c.id, "name": c.name, "status": f"{before} → {status}", "partial_kept": moved}
        )
    session.flush()
    return out


def main() -> None:
    from app.db.session import get_engine

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with Session(get_engine()) as s:
        rows = recheck_profiles(s)
        s.commit()
    for r in rows:
        print(f"#{r['id']} {r['name']}: {r['status']}, {r['partial_kept']} partial statements kept")
    print(f"{len(rows)} profiles moved to check rule {CHECK_RULE} (no LLM calls)")


if __name__ == "__main__":
    main()
