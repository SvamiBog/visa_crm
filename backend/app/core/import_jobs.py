"""Checks and imports of a workbook that run in the background (backlog 1.21,
docs/data-format.md → «Проверка и загрузка»). A request held open while a whole history
loads was cut by the hosting's proxy (Railway closes a request that sends nothing for five
minutes) and by phones going to sleep, and the page said «нет связи с сервером». So the
request only starts the run; the page asks how far it is.

The runs live in the memory of the one web process (one service, one replica, see
docs/deploy.md): a restart loses them, and the import, one transaction, rolls back whole.
A workspace has one run at a time; a new check stops a check still running, a running
import refuses a new run. A finished run is kept an hour for the page to collect.
"""

import asyncio
import logging
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.data_import import ImportStopped, Progress

logger = logging.getLogger(__name__)

KEEP_FINISHED = 3600  # seconds

Status = Literal["running", "done", "failed", "stopped"]
Work = Callable[[Progress], Awaitable[Any]]


class ImportRunning(RuntimeError):
    """The workspace's import is still running: a new run waits for it."""


@dataclass
class ImportJob:
    id: uuid.UUID
    tenant_id: uuid.UUID
    save: bool  # an import; else a check, which writes nothing
    file_name: str
    progress: Progress = field(default_factory=Progress)
    status: Status = "running"
    # What the work returned: the report, for the API.
    result: Any = None
    # An error code (errors.* in the locales), never the error's text.
    error: str | None = None
    finished_at: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


_jobs: dict[uuid.UUID, ImportJob] = {}


def start(tenant_id: uuid.UUID, *, save: bool, file_name: str, work: Work) -> ImportJob:
    """Run `work` in the background for the workspace; raises ImportRunning while its
    import runs."""
    _forget_finished()
    current = running(tenant_id)
    if any(job.save for job in current):
        raise ImportRunning
    for job in current:
        job.progress.stop = True
    job = ImportJob(id=uuid.uuid4(), tenant_id=tenant_id, save=save, file_name=file_name)
    _jobs[job.id] = job
    # The task is kept on the job: the loop holds only a weak reference to it.
    job.task = asyncio.create_task(_run(job, work))
    return job


def get(job_id: uuid.UUID, tenant_id: uuid.UUID) -> ImportJob | None:
    """The workspace's run; another workspace's is not found."""
    job = _jobs.get(job_id)
    return job if job is not None and job.tenant_id == tenant_id else None


def running(tenant_id: uuid.UUID) -> list[ImportJob]:
    return [j for j in _jobs.values() if j.tenant_id == tenant_id and j.status == "running"]


async def _run(job: ImportJob, work: Work) -> None:
    try:
        job.result = await work(job.progress)
        job.status = "done"
    except ImportStopped:
        job.status = "stopped"
    except Exception as error:
        # The message may carry a value of the file (a database error quotes the row): the
        # log gets the type and the frames it went through, never the text.
        where = "\n".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(error.__traceback__)
        )
        logger.error("Import job %s failed: %s\n%s", job.id, type(error).__name__, where)
        job.status = "failed"
        job.error = "import_failed"
    finally:
        job.finished_at = time.monotonic()


def _forget_finished() -> None:
    now = time.monotonic()
    for job_id in [
        j.id
        for j in _jobs.values()
        if j.finished_at is not None and now - j.finished_at > KEEP_FINISHED
    ]:
        del _jobs[job_id]
