"""Импорт в формате VisaCRM (backlog 1.21, docs/data-format.md): the specialist downloads
the template and the prompt, has an AI turn her spreadsheets into one workbook of the
format, checks what loading it would do and loads it. The file is not kept: it is sent
again for the check and for the import. Only the workspace owner imports.

The check and the import run in the background (app/core/import_jobs.py): the request
reads the file, starts the run and answers at once; the page asks /imports/jobs/{id} how
far it is until the report is ready.

The reader is app/core/workbook.py, the engine app/core/data_import.py; the rules are in
docs/decisions.md → «Формат данных VisaCRM».
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import Base64Bytes, BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    TenantSession,
    effective_modules,
    require_module,
    require_specialist,
    workspace_today,
)
from app.core import import_jobs
from app.core.audit import record
from app.core.data_export import Lists, template
from app.core.data_format import CREATABLE
from app.core.data_import import NEW, SKIP, Progress, WorkbookImport
from app.core.db import get_sessionmaker, scope_to_tenant
from app.core.documents import content_disposition
from app.core.import_jobs import ImportJob, ImportRunning
from app.core.import_prompt import import_prompt
from app.core.workbook import Workbook, WorkbookError, read_workbook
from app.models import (
    AuditAction,
    Country,
    CountryRule,
    ImportKind,
    ImportRun,
    LeadChannel,
    Partner,
    Registrar,
    Tenant,
    User,
    VaultSystem,
    VisaCenter,
    VisaPurpose,
)

router = APIRouter(
    prefix="/imports", tags=["imports"], dependencies=[Depends(require_module("clients"))]
)
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

# Values a file may leave unknown in one list, each with the specialist's choice.
MAX_CHOICES = 1000
HISTORY_LIMIT = 50
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Language = Literal["ru", "en"]


class ImportIn(BaseModel):
    file_name: Annotated[str, Field(min_length=1, max_length=200)]
    content: Base64Bytes
    # An unknown list value: type → normalized value → "new", "skip" or an entry's id.
    choices: Annotated[dict[str, dict[str, str]], Field(max_length=len(CREATABLE))] = {}

    @field_validator("choices")
    @classmethod
    def known_choices(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        types = {t.value for t in CREATABLE}
        for kind, choices in value.items():
            if kind not in types or len(choices) > MAX_CHOICES:
                raise ValueError("invalid_choices")
            for key, choice in choices.items():
                if len(key) > 200 or not _choice(choice):
                    raise ValueError("invalid_choices")
        return value


def _choice(choice: str) -> bool:
    if choice in (NEW, SKIP):
        return True
    try:
        uuid.UUID(choice)
    except ValueError:
        return False
    return True


class IssueOut(BaseModel):
    # Runs of the old import (1.14, 1.16) had one sheet: no sheet, a row always.
    sheet: str | None = None
    row: int | None
    column: str | None = None
    reason: str


class UnknownOut(BaseModel):
    key: str
    value: str
    rows: int


class ReportOut(BaseModel):
    id: uuid.UUID | None
    kind: ImportKind
    file_name: str
    created_at: datetime | None
    rows: int
    imported: int
    skipped: list[IssueOut]
    skipped_count: int
    warnings: list[IssueOut]
    warnings_count: int
    created: dict[str, int]
    updated: dict[str, int]
    unknown: dict[str, list[UnknownOut]]


class RunSummary(BaseModel):
    id: uuid.UUID
    kind: ImportKind
    file_name: str
    rows: int
    imported: int
    skipped_count: int
    warnings_count: int
    created_by: str | None
    created_at: datetime


class JobOut(BaseModel):
    """A check or an import running in the background, and its report once it is done."""

    id: uuid.UUID
    kind: Literal["check", "import"]
    file_name: str
    status: import_jobs.Status
    stage: Literal["rates", "rows"]
    rates_done: int
    rates_total: int
    rows_done: int
    rows_total: int
    report: ReportOut | None
    error: str | None


class PromptOut(BaseModel):
    text: str


# --- Helpers --------------------------------------------------------------------------


def _read(body: ImportIn) -> Workbook:
    try:
        return read_workbook(body.content)
    except WorkbookError as error:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code in ("file_too_large", "too_many_rows")
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, error.code) from error


async def _import(
    book: Workbook,
    body: ImportIn,
    user: CurrentUser,
    session: AsyncSession,
    *,
    save: bool,
    progress: Progress,
) -> tuple[ImportRun, dict[str, Any]]:
    """Run the import in the session; the caller commits it or lets it roll back."""
    assert user.tenant_id is not None
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    run = ImportRun(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        kind=ImportKind.WORKBOOK,
        file_name=body.file_name,
        sheet=None,
        rows=0,
        report={},
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    engine = WorkbookImport(
        session,
        tenant=tenant,
        user_id=user.id,
        modules=await effective_modules(session, user),
        choices=body.choices,
        today=await workspace_today(session, user.tenant_id),
        run_id=run.id,
        progress=progress,
    )
    report = (await engine.run(book)).as_json()
    run.report = report
    run.rows = report["rows"]
    if save:
        record(
            session,
            AuditAction.IMPORT_COMPLETED,
            actor=user,
            target_type="import",
            target_id=run.id,
            details={
                "kind": ImportKind.WORKBOOK.value,
                "rows": run.rows,
                "imported": report["imported"],
                "skipped": report["skipped_count"],
                "created": report["created"],
                "updated": report["updated"],
            },
        )
    return run, report


def _report_out(run: ImportRun, report: dict[str, Any], *, saved: bool) -> ReportOut:
    return ReportOut(
        id=run.id if saved else None,
        kind=run.kind,
        file_name=run.file_name,
        created_at=run.created_at if saved else None,
        **report,
    )


def _start(body: ImportIn, user: CurrentUser, *, save: bool) -> JobOut:
    """Read the file now (a file that cannot be read is refused at once), run the rest in
    the background, in a session of its own: the request's closes when it answers."""
    assert user.tenant_id is not None
    tenant_id = user.tenant_id
    book = _read(body)

    async def work(progress: Progress) -> ReportOut:
        async with get_sessionmaker()() as session:
            scope_to_tenant(session, tenant_id)
            run, report = await _import(book, body, user, session, save=save, progress=progress)
            if not save:
                out = _report_out(run, report, saved=False)
                await session.rollback()
                return out
            await session.commit()
            await session.refresh(run)
            return _report_out(run, report, saved=True)

    try:
        job = import_jobs.start(tenant_id, save=save, file_name=body.file_name, work=work)
    except ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    return _job_out(job)


def _job_out(job: ImportJob) -> JobOut:
    p = job.progress
    return JobOut(
        id=job.id,
        kind="import" if job.save else "check",
        file_name=job.file_name,
        status=job.status,
        stage=p.stage,
        rates_done=p.rates_done,
        rates_total=p.rates_total,
        rows_done=p.rows_done,
        rows_total=p.rows_total,
        report=job.result,
        error=job.error,
    )


async def _names(session: AsyncSession, model: Any) -> list[str]:
    rows = await session.scalars(select(model.name).where(model.is_active).order_by(model.name))
    return list(rows)


async def _lists(session: AsyncSession, language: Language) -> Lists:
    """The workspace's values for the template's «Справочник»."""
    country = Country.name_ru if language == "ru" else Country.name_en
    # The English name of a city is optional: the Russian one stands in for it.
    city = (
        VisaCenter.city_ru
        if language == "ru"
        else func.coalesce(VisaCenter.city_en, VisaCenter.city_ru)
    )
    cities = await session.scalars(
        select(city).join(CountryRule, VisaCenter.rule_id == CountryRule.id).distinct()
    )
    return Lists(
        countries=list(await session.scalars(select(country).order_by(country))),
        cities=sorted(set(cities)),
        channels=await _names(session, LeadChannel),
        partners=await _names(session, Partner),
        purposes=await _names(session, VisaPurpose),
        registrars=await _names(session, Registrar),
        systems=await _names(session, VaultSystem),
    )


# --- Endpoints ------------------------------------------------------------------------


@router.post("/check", status_code=status.HTTP_202_ACCEPTED)
async def check(body: ImportIn, user: Specialist) -> JobOut:
    """Start finding what the import would do, without writing anything."""
    return _start(body, user, save=False)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def run_import(body: ImportIn, user: Specialist) -> JobOut:
    return _start(body, user, save=True)


@router.get("/jobs")
async def running_jobs(user: Specialist) -> list[JobOut]:
    """The workspace's run still going: the page shows it after a reload."""
    assert user.tenant_id is not None
    return [_job_out(job) for job in import_jobs.running(user.tenant_id)]


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, user: Specialist) -> JobOut:
    """How far a run is, with its report once done. Another workspace's run, or one the
    server lost in a restart, is not found."""
    assert user.tenant_id is not None
    job = import_jobs.get(job_id, user.tenant_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import_job_not_found")
    return _job_out(job)


@router.get("")
async def list_runs(user: Specialist, session: TenantSession) -> list[RunSummary]:
    rows = await session.execute(
        select(ImportRun, User.email)
        .outerjoin(User, User.id == ImportRun.created_by)
        .order_by(ImportRun.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    return [
        RunSummary(
            id=run.id,
            kind=run.kind,
            file_name=run.file_name,
            rows=run.rows,
            imported=run.report.get("imported", 0),
            skipped_count=run.report.get("skipped_count", 0),
            warnings_count=run.report.get("warnings_count", 0),
            created_by=email,
            created_at=run.created_at,
        )
        for run, email in rows
    ]


@router.get("/template")
async def download_template(
    user: Specialist, session: TenantSession, language: Language = "ru"
) -> Response:
    """The empty workbook of the format with the workspace's lists, for the AI."""
    assert user.tenant_id is not None
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    content = template(
        await _lists(session, language),
        language=language,
        workspace=tenant.name,
        base_currency=tenant.base_currency,
    )
    name = "VisaCRM-шаблон.xlsx" if language == "ru" else "VisaCRM-template.xlsx"
    return Response(
        content, media_type=XLSX, headers={"Content-Disposition": content_disposition(name)}
    )


@router.get("/prompt")
async def get_prompt(user: Specialist, language: Annotated[Language, Query()] = "ru") -> PromptOut:
    """The universal prompt (docs/import-prompt.md) in the interface's language."""
    return PromptOut(text=import_prompt(language))


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, user: Specialist, session: TenantSession) -> ReportOut:
    run = await session.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _report_out(run, run.report, saved=True)
