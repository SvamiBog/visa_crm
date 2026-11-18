"""Where a client stands in the client index (картотека, backlog 1.5): every contact is in
exactly one tab, computed in SQL so search, filters, paging and counts agree.

- «В работе» (work): a case not at the result stage.
- «Думают» (thinking): otherwise, the latest consultation ended with «thinking».
- «Консультации» (consult): otherwise, the latest consultation is undecided or «yes»
  without cases opened from it yet; also a new contact with no consultations or cases.
- «Архив» (archive): the rest — a «no», or all cases at the result stage.
"""

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import Select, and_, any_, case, exists, null, or_, select, true
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Lateral

from app.models import CaseStage, Consultation, Contact, Decision, VisaCase

# Names in the order of the alphabet whatever the database's own locale: ICU's root
# collation puts «Ё» with «Е» (a plain code-point order would put it before «А»).
NAME_COLLATION = "und-x-icu"


class Tab(StrEnum):
    ALL = "all"
    WORK = "work"
    CONSULT = "consult"
    THINKING = "thinking"
    ARCHIVE = "archive"


TABS = tuple(t for t in Tab if t != Tab.ALL)


class Sort(StrEnum):
    """Order of the index: the newest first unless a column is chosen; «-» is descending."""

    NEWEST = "newest"
    NAME = "name"
    NAME_DESC = "-name"
    NEXT = "next"
    NEXT_DESC = "-next"


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


def shown_case() -> Lateral:
    """The case a row of the index shows (`_rows` in app/api/contacts.py): the newest one
    in work, otherwise the newest one."""
    return (
        select(VisaCase.id, VisaCase.outcome, VisaCase.submission_on)
        .where(VisaCase.contact_id == Contact.id)
        .order_by((VisaCase.stage == CaseStage.RESULT).asc(), VisaCase.number.desc())
        .limit(1)
        .lateral("shown")
    )


def next_date(latest: Lateral, shown: Lateral | None) -> ColumnElement[date | None]:
    """The date in the «Ближайшее» column (`next` in frontend/src/pages/clients/
    ClientsPage.tsx): the submission of the shown case until its result; without a case,
    the latest consultation. `shown` is None without the «cases» module."""
    if shown is None:
        return latest.c.held_on
    return case(
        (shown.c.id.is_(None), latest.c.held_on),
        (shown.c.outcome.is_not(None), null()),
        else_=shown.c.submission_on,
    )


def sort_contacts(query: Select, latest: Lateral, sort: Sort, with_cases: bool) -> Select:
    """Orders the index; ties (and the default) go newest first, so pages never overlap.
    Rows without a next date stay at the end in both directions."""
    newest = (Contact.created_at.desc(), Contact.id.desc())
    if sort in (Sort.NAME, Sort.NAME_DESC):
        name = Contact.full_name.collate(NAME_COLLATION)
        return query.order_by(name.desc() if sort == Sort.NAME_DESC else name, *newest)
    if sort in (Sort.NEXT, Sort.NEXT_DESC):
        shown = shown_case() if with_cases else None
        if shown is not None:
            query = query.outerjoin(shown, true())
        due = next_date(latest, shown)
        due = due.desc() if sort == Sort.NEXT_DESC else due.asc()
        return query.order_by(due.nulls_last(), *newest)
    return query.order_by(*newest)


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
                any_(latest.c.destination_ids) == country_id,
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
