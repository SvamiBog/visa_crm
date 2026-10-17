"""Which items of a document checklist a case gets (backlog 2.2; the package check of a
case, 2.3, goes by the same rule).

An item is for any submission country unless it names one, and for any purpose unless
it lists purpose kinds: then only for a case whose visa purpose is of one of those kinds.
A purpose the specialist has not given a kind gets only the items for any purpose.
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import Protocol

from app.models import PurposeKind

_ORDER = {kind: i for i, kind in enumerate(PurposeKind)}


class Scoped(Protocol):
    @property
    def submission_id(self) -> uuid.UUID | None: ...

    @property
    def purposes(self) -> Sequence[PurposeKind]: ...


def for_submission(item: Scoped, submission_id: uuid.UUID) -> bool:
    return item.submission_id is None or item.submission_id == submission_id


def for_purpose(item: Scoped, purpose: PurposeKind | None) -> bool:
    return not item.purposes or purpose in item.purposes


def applies(item: Scoped, submission_id: uuid.UUID, purpose: PurposeKind | None) -> bool:
    """The item is in the checklist of a case submitted there for a purpose of this kind."""
    return for_submission(item, submission_id) and for_purpose(item, purpose)


def normalize_purposes(kinds: Iterable[PurposeKind | str]) -> list[PurposeKind]:
    """Each kind once, in the order of PurposeKind."""
    return sorted({PurposeKind(k) for k in kinds}, key=_ORDER.__getitem__)
