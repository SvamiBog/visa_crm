"""Where a client stands in the client index (картотека, backlog 1.5): every contact is in
exactly one tab, computed in SQL so search, filters, paging and counts agree.

- «В работе» (work): a case not at the result stage — the contact's own, or one opened
  from the contact's consultation for someone else (the daughter came, the case is her
  father's: pilot fix 2).
- «Думают» (thinking): otherwise, the latest consultation ended with «thinking».
- «Консультации» (consult): otherwise, the latest consultation is undecided or «yes»
  and not covered by a case yet (`covers`); also a new contact with no consultations or
  cases.
- «Архив» (archive): the rest — a «no», or all cases at the result stage.

Without the «cases» module the index tells nothing about cases (audit L3, owner's
decision 2026-11-22): tabs and counts go as if the contact had no cases, and the filters
by a case's stage, registrar and destination are left out.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Date,
    Select,
    Uuid,
    and_,
    any_,
    case,
    cast,
    exists,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Lateral

from app.core.clock import DEFAULT_TIMEZONE
from app.models import CaseStage, Consultation, Contact, Decision, Tenant, VisaCase

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
            Consultation.created_at,
        )
        .where(Consultation.contact_id == Contact.id)
        .order_by(Consultation.held_on.desc(), Consultation.created_at.desc())
        .limit(1)
        .lateral("latest")
    )


def _opened_for_someone_else(contact_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    """Joins `VisaCase` to a consultation of the contact it was opened from for someone
    else (pilot fix 2) — unless the contact is an applicant of that consultation
    themselves: then their own case stands for them, not the family's."""
    theirs, mine = aliased(Consultation), aliased(VisaCase)
    applicant_too = (
        exists()
        .where(mine.consultation_id == theirs.id, mine.contact_id == contact_id)
        .correlate_except(mine)
    )
    return and_(
        VisaCase.consultation_id == theirs.id, theirs.contact_id == contact_id, ~applicant_too
    )


def has_case(
    contact_id: ColumnElement[uuid.UUID], *criteria: ColumnElement[bool]
) -> ColumnElement[bool]:
    """Whether the contact goes with a case that meets `criteria`: their own, or one
    opened from their consultation for someone else. The same rule is applied in Python
    to the rows of a page (`_rows` in app/api/contacts.py): keep the two in step.

    Two EXISTS, not one with «own OR from their consultation» inside: each then finds its
    cases by an index (contact_id, consultation_id). One condition with the OR left the
    database no index to use, and it read every case once per contact (audit 2026-11-27,
    H-1)."""
    own = exists().where(VisaCase.contact_id == contact_id, *criteria)
    family = exists().where(_opened_for_someone_else(contact_id), *criteria)
    return or_(own, family)


def _workspace_timezone() -> ColumnElement[str]:
    """The specialist's time zone, read once per query from the session's workspace
    (app.tenant_id, app/core/db.py): an uncorrelated subquery Postgres runs once."""
    workspace = cast(func.nullif(func.current_setting("app.tenant_id", True), ""), Uuid)
    return func.coalesce(
        select(Tenant.timezone).where(Tenant.id == workspace).scalar_subquery(),
        DEFAULT_TIMEZONE,
    )


def covers(
    consultation_id: ColumnElement[uuid.UUID],
    contact_id: ColumnElement[uuid.UUID],
    held_on: ColumnElement[date],
    created_at: ColumnElement[datetime],
) -> ColumnElement[bool]:
    """A case covers a consultation — «кейс открыт» — when it was opened from it, or when
    it is the consultation's contact's own case not from another consultation of theirs
    (from a family member's consultation — Nikita in Sofia's group case, pilot fix 8 — or
    without one) and this is the contact's latest consultation held on or before the day
    the case was opened. Older consultations keep their decision and their «Создать
    кейс»; a returning client's case of an earlier consultation of their own covers only
    that one. The latest is by (held_on, created_at), as `latest_consultation`."""
    own, newer = aliased(Consultation), aliased(Consultation)
    # The day the case was opened in the specialist's time zone, as the consultation's.
    opened_on = cast(func.timezone(_workspace_timezone(), VisaCase.created_at), Date)
    newer_before_case = (
        exists()
        .where(
            newer.contact_id == contact_id,
            newer.held_on <= opened_on,
            or_(
                newer.held_on > held_on,
                and_(newer.held_on == held_on, newer.created_at > created_at),
            ),
        )
        .correlate_except(newer)
    )
    return or_(
        VisaCase.consultation_id == consultation_id,
        and_(
            VisaCase.contact_id == contact_id,
            held_on <= opened_on,
            or_(
                VisaCase.consultation_id.is_(None),
                ~exists()
                .where(own.id == VisaCase.consultation_id, own.contact_id == contact_id)
                .correlate_except(own),
            ),
            ~newer_before_case,
        ),
    )


def tab_of(latest: Lateral, *, with_cases: bool = True) -> ColumnElement[str]:
    """The contact's tab; without the «cases» module, as if they had no cases."""
    if not with_cases:
        return case(
            (latest.c.decision == Decision.THINKING, Tab.THINKING.value),
            (latest.c.id.is_(None), Tab.CONSULT.value),
            (latest.c.decision.is_(None), Tab.CONSULT.value),
            (latest.c.decision == Decision.YES, Tab.CONSULT.value),
            else_=Tab.ARCHIVE.value,
        )
    in_work = has_case(Contact.id, VisaCase.stage != CaseStage.RESULT)
    any_case = exists().where(VisaCase.contact_id == Contact.id)
    covered = exists().where(covers(latest.c.id, Contact.id, latest.c.held_on, latest.c.created_at))
    return case(
        (in_work, Tab.WORK.value),
        (latest.c.decision == Decision.THINKING, Tab.THINKING.value),
        (and_(latest.c.id.is_not(None), latest.c.decision.is_(None)), Tab.CONSULT.value),
        (and_(latest.c.decision == Decision.YES, ~covered), Tab.CONSULT.value),
        (and_(latest.c.id.is_(None), ~any_case), Tab.CONSULT.value),
        else_=Tab.ARCHIVE.value,
    )


def shown_case() -> Lateral:
    """The case a row of the index shows (`_rows` in app/api/contacts.py): the newest one
    in work, otherwise the newest one; the contact's own before one opened from their
    consultation for someone else. The two kinds are read apart and put together, each by
    its index (see `has_case`)."""

    def kind(own: bool, *where: ColumnElement[bool]) -> Select:
        return (
            select(
                VisaCase.id,
                VisaCase.outcome,
                VisaCase.submission_on,
                VisaCase.stage,
                VisaCase.number,
                literal(own).label("own"),
            )
            .where(*where)
            .correlate(Contact)
        )

    cases = union_all(
        kind(True, VisaCase.contact_id == Contact.id),
        kind(False, _opened_for_someone_else(Contact.id)),
    ).subquery("their_cases")
    return (
        select(cases.c.id, cases.c.outcome, cases.c.submission_on)
        .order_by(
            (cases.c.stage == CaseStage.RESULT).asc(), cases.c.own.desc(), cases.c.number.desc()
        )
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
    with_cases: bool,
) -> Select:
    """Country: of a case or of the latest consultation. Stage and registrar: of a case —
    the contact's own or one opened from their consultation (`has_case`). Without the
    «cases» module only the consultation counts: the filters by a case are left out, so
    they neither narrow the list nor tell anything about the cases."""
    if channel_id is not None:
        query = query.where(Contact.lead_channel_id == channel_id)
    if country_id is not None:
        by_consultation = any_(latest.c.destination_ids) == country_id
        if with_cases:
            by_case = has_case(Contact.id, VisaCase.destination_id == country_id)
            query = query.where(or_(by_case, by_consultation))
        else:
            query = query.where(by_consultation)
    if not with_cases:
        return query
    if stage is not None:
        query = query.where(has_case(Contact.id, VisaCase.stage == stage))
    if registrar_id is not None:
        query = query.where(has_case(Contact.id, VisaCase.registrar_id == registrar_id))
    return query
