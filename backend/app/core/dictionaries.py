"""Starting lists for a new workspace (docs/clients-module.md → «Справочники»).

The specialist renames, adds and switches them off; these only save typing on day one."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadChannel, VisaPurpose

DEFAULT_PURPOSES = ("Туризм", "Гостевая", "Бизнес", "Учёба")
DEFAULT_CHANNELS = ("Instagram", "Сарафан", "Партнёр", "Друзья", "Реклама")


def add_default_dictionaries(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for order, name in enumerate(DEFAULT_PURPOSES):
        session.add(VisaPurpose(tenant_id=tenant_id, name=name, sort_order=order))
    for order, name in enumerate(DEFAULT_CHANNELS):
        session.add(LeadChannel(tenant_id=tenant_id, name=name, sort_order=order))
