"""Starting lists for a new workspace (docs/clients-module.md → «Справочники»).

The specialist renames, adds and switches them off; these only save typing on day one."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadChannel, PurposeKind, VaultSystem, VaultSystemKind, VisaPurpose

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
