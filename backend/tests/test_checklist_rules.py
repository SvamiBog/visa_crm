"""Which checklist items a case gets (backlog 2.2, app/core/checklists.py)."""

import uuid
from dataclasses import dataclass, field

import pytest

from app.core.checklists import applies, for_purpose, for_submission, normalize_purposes
from app.models import PurposeKind as K

BELARUS = uuid.uuid4()
TURKEY = uuid.uuid4()


@dataclass
class Item:
    submission_id: uuid.UUID | None = None
    purposes: list[K] = field(default_factory=list)


def test_an_item_for_anyone_applies_to_every_case():
    item = Item()
    for submission in (BELARUS, TURKEY):
        for purpose in (*K, None):
            assert applies(item, submission, purpose)


def test_an_item_for_one_submission_country_applies_only_there():
    permit = Item(submission_id=TURKEY)  # a residence permit when applying in Turkey
    assert applies(permit, TURKEY, K.TOURISM)
    assert not applies(permit, BELARUS, K.TOURISM)


def test_an_item_for_some_purposes_applies_only_to_them():
    invitation = Item(purposes=[K.VISIT, K.BUSINESS])
    assert applies(invitation, BELARUS, K.VISIT)
    assert applies(invitation, BELARUS, K.BUSINESS)
    assert not applies(invitation, BELARUS, K.TOURISM)
    # A purpose the specialist has not given a kind gets the items for any purpose only.
    assert not applies(invitation, BELARUS, None)


def test_both_conditions_must_hold():
    item = Item(submission_id=TURKEY, purposes=[K.WORK])
    assert applies(item, TURKEY, K.WORK)
    assert not applies(item, BELARUS, K.WORK)
    assert not applies(item, TURKEY, K.TOURISM)


def test_each_condition_can_be_checked_alone():
    # The checklist page narrows by a submission country or by a purpose, or by both.
    item = Item(submission_id=TURKEY, purposes=[K.WORK])
    assert for_submission(item, TURKEY) and not for_submission(item, BELARUS)
    assert for_purpose(item, K.WORK) and not for_purpose(item, K.STUDY)


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ([], []),
        ([K.WORK, K.TOURISM, K.WORK], [K.TOURISM, K.WORK]),
        (["visit", "tourism"], [K.TOURISM, K.VISIT]),
    ],
)
def test_purposes_are_kept_once_in_a_fixed_order(given, kept):
    assert normalize_purposes(given) == kept
