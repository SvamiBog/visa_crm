"""Where a client stands in the client index (картотека, backlog 1.5): every contact is in
exactly one tab, computed in SQL so search, filters, paging and counts agree.

- «В работе» (work): a case not at the result stage.
- «Думают» (thinking): otherwise, the latest consultation ended with «thinking».
- «Консультации» (consult): otherwise, the latest consultation is undecided or «yes»
  without cases opened from it yet; also a new contact with no consultations or cases.
- «Архив» (archive): the rest — a «no», or all cases at the result stage.
"""

import uuid
from enum import StrEnum

from sqlalchemy import Select, and_, case, exists, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Lateral

from app.models import CaseStage, Consultation, Contact, Decision, VisaCase


class Tab(StrEnum):
    ALL = "all"
    WORK = "work"
    CONSULT = "consult"
    THINKING = "thinking"
    ARCHIVE = "archive"


TABS = tuple(t for t in Tab if t != Tab.ALL)


def latest_consultation() -> Lateral:
    """The contact's latest consultation, joined to each contact row."""
    return (
        select(
            Consultation.id,
            Consultation.decision,
            Consultation.held_on,
            Consultation.destination_ids,
        )
        .where(Consultation.contact_id == Contact.id)
        .order_by(Consultation.held_on.desc(), Consultation.created_at.desc())
        .limit(1)
        .lateral("latest")
    )


def tab_of(latest: Lateral) -> ColumnElement[str]:
    in_work = exists().where(VisaCase.contact_id == Contact.id, VisaCase.stage != CaseStage.RESULT)
    any_case = exists().where(VisaCase.contact_id == Contact.id)
    opened_from_latest = exists().where(VisaCase.consultation_id == latest.c.id)
    return case(
        (in_work, Tab.WORK.value),
        (latest.c.decision == Decision.THINKING, Tab.THINKING.value),
        (and_(latest.c.id.is_not(None), latest.c.decision.is_(None)), Tab.CONSULT.value),
        (and_(latest.c.decision == Decision.YES, ~opened_from_latest), Tab.CONSULT.value),
        (and_(latest.c.id.is_(None), ~any_case), Tab.CONSULT.value),
        else_=Tab.ARCHIVE.value,
    )


def filter_contacts(
    query: Select,
    latest: Lateral,
    *,
    country_id: uuid.UUID | None = None,
    stage: CaseStage | None = None,
    channel_id: uuid.UUID | None = None,
    registrar_id: uuid.UUID | None = None,
) -> Select:
    """Country: of a case or of the latest consultation. Stage and registrar: of a case."""
    if channel_id is not None:
        query = query.where(Contact.lead_channel_id == channel_id)
    if country_id is not None:
        query = query.where(
            or_(
                exists().where(
                    VisaCase.contact_id == Contact.id, VisaCase.destination_id == country_id
                ),
                latest.c.destination_ids.any(country_id),
            )
        )
    if stage is not None:
        query = query.where(
            exists().where(VisaCase.contact_id == Contact.id, VisaCase.stage == stage)
        )
    if registrar_id is not None:
        query = query.where(
            exists().where(VisaCase.contact_id == Contact.id, VisaCase.registrar_id == registrar_id)
        )
    return query
