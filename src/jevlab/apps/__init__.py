"""アプリ一覧。新しいアプリは apps/<name>/spec.py に SPEC を定義し、ここに追加する。"""

from __future__ import annotations

from typing import Final

from jevlab.apps.commit.spec import SPEC as COMMIT
from jevlab.apps.contract.spec import SPEC as CONTRACT
from jevlab.apps.incident.spec import SPEC as INCIDENT
from jevlab.apps.mail.spec import SPEC as MAIL
from jevlab.apps.moderation.spec import SPEC as MODERATION
from jevlab.apps.rewrite.spec import SPEC as REWRITE
from jevlab.apps.slop.spec import SPEC as SLOP
from jevlab.apps.triage.spec import SPEC as TRIAGE
from jevlab.core.engine import AppSpec

APPS: Final[dict[str, AppSpec]] = {
    spec.name: spec for spec in (MAIL, TRIAGE, INCIDENT, MODERATION, COMMIT, CONTRACT, SLOP, REWRITE)
}
