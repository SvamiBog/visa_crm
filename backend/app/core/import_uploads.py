"""Files waiting to be loaded (backlog 1.22, docs/data-format.md → «Проверка и загрузка»).

The specialist uploads her workbook once. It waits on the server while she looks at a few
of its cards, has the whole file checked, looks through every card and confirms it; only
then is it loaded into the CRM.

The files live in the memory of the one web process, as the runs do (app/core/import_jobs.py):
a restart loses them, and the page asks for the file again. A workspace has one file at a
time; it is forgotten KEEP after its last use, on «Отменить» and once it is loaded. The
cards hold the clients' data: they are kept here only, never in the database or the log.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.import_cards import Card

KEEP = 24 * 3600  # seconds after the last use


@dataclass
class Upload:
    id: uuid.UUID
    tenant_id: uuid.UUID
    file_name: str
    content: bytes
    # The file's sheets with their rows, and the rows of «Клиенты».
    sheets: list[tuple[str, int]]
    clients: int
    used_at: float
    # The cards of the sample, and those of the whole file with the report of its check.
    sample: list[Card] | None = None
    cards: list[Card] | None = None
    report: Any = None


_uploads: dict[uuid.UUID, Upload] = {}


def _now() -> float:
    return time.monotonic()


def put(
    tenant_id: uuid.UUID,
    *,
    file_name: str,
    content: bytes,
    sheets: list[tuple[str, int]],
    clients: int,
) -> Upload:
    """Keep the workspace's new file in place of the one waiting."""
    _forget_old()
    for upload in [u for u in _uploads.values() if u.tenant_id == tenant_id]:
        del _uploads[upload.id]
    upload = Upload(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        file_name=file_name,
        content=content,
        sheets=sheets,
        clients=clients,
        used_at=_now(),
    )
    _uploads[upload.id] = upload
    return upload


def get(upload_id: uuid.UUID, tenant_id: uuid.UUID) -> Upload | None:
    """The workspace's file; another workspace's is not found."""
    _forget_old()
    upload = _uploads.get(upload_id)
    if upload is None or upload.tenant_id != tenant_id:
        return None
    upload.used_at = _now()
    return upload


def current(tenant_id: uuid.UUID) -> Upload | None:
    _forget_old()
    upload = next((u for u in _uploads.values() if u.tenant_id == tenant_id), None)
    if upload is not None:
        upload.used_at = _now()
    return upload


def drop(upload: Upload) -> None:
    _uploads.pop(upload.id, None)


def _forget_old() -> None:
    now = _now()
    for upload_id in [u.id for u in _uploads.values() if now - u.used_at > KEEP]:
        del _uploads[upload_id]
