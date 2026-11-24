"""Starting lists for a new workspace (docs/clients-module.md → «Справочники») and the
place a new entry takes in its list.

The specialist renames, adds and switches them off; these only save typing on day one."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadChannel, PurposeKind, VaultSystem, VaultSystemKind, VisaPurpose

# The highest place in a list of purposes, channels or vault systems the API takes.
SORT_ORDER_MAX = 999


def next_sort_order(last: int | None) -> int:
    """A new entry goes after the last one (backlog 1.15.14). A list that already reaches
    the highest place keeps new entries there too, in the order of their names: adding to
    a list never fails on its order."""
    return 0 if last is None else min(last + 1, SORT_ORDER_MAX)


DEFAULT_PURPOSES = (
    ("Туризм", PurposeKind.TOURISM),
    ("Гостевая", PurposeKind.VISIT),
    ("Бизнес", PurposeKind.BUSINESS),
    ("Учёба", PurposeKind.STUDY),
)
DEFAULT_CHANNELS = ("Instagram", "Сарафан", "Партнёр", "Друзья", "Реклама")
# Sites accounts are made on; password rules start at the defaults and the specialist
# sets each site's own.
DEFAULT_SYSTEMS = (
    ("Почта", VaultSystemKind.EMAIL),
    ("VFS Global", VaultSystemKind.VISA_CENTER),
    ("TLScontact", VaultSystemKind.VISA_CENTER),
    ("BLS International", VaultSystemKind.VISA_CENTER),
    ("Сайт посольства", VaultSystemKind.EMBASSY),
    ("Booking.com", VaultSystemKind.BOOKING),
)


def add_default_dictionaries(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for order, (name, kind) in enumerate(DEFAULT_PURPOSES):
        session.add(VisaPurpose(tenant_id=tenant_id, name=name, kind=kind, sort_order=order))
    for order, name in enumerate(DEFAULT_CHANNELS):
        session.add(LeadChannel(tenant_id=tenant_id, name=name, sort_order=order))
    for order, (name, kind) in enumerate(DEFAULT_SYSTEMS):
        session.add(VaultSystem(tenant_id=tenant_id, name=name, kind=kind, sort_order=order))
