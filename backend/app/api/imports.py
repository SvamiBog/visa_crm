"""Импорт в формате VisaCRM (backlog 1.21, 1.22, docs/data-format.md): the specialist downloads
the template and the prompt, has an AI turn her spreadsheets into one workbook of the format
and uploads it. The file waits on the server (app/core/import_uploads.py): first a few sample
cards, then the check of the whole file with every card it will make, and only once she has
looked through them and confirmed it is the file loaded into the CRM. Only the workspace
owner imports.

The sample, the check and the import run in the background (app/core/import_jobs.py): the
request starts the run and answers at once; the page asks /imports/jobs/{id} how far it is.

The reader is app/core/workbook.py, the engine app/core/data_import.py, the cards
app/core/import_cards.py; the rules are in docs/decisions.md → «Формат данных VisaCRM» and
«Карточки до загрузки».
"""

import asyncio
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
from app.core import import_jobs, import_uploads
from app.core.audit import record
from app.core.data_export import Lists, template
from app.core.data_format import CREATABLE
from app.core.data_import import NEW, SKIP, Progress, WorkbookImport
from app.core.db import get_sessionmaker, scope_to_tenant
from app.core.documents import content_disposition
from app.core.import_cards import cards, render, summary
from app.core.import_jobs import ImportJob, ImportRunning
from app.core.import_prompt import import_prompt
from app.core.import_uploads import Upload
from app.core.workbook import Workbook, WorkbookError, first_clients, read_workbook
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
# The clients whose cards are shown before the whole file is gone through.
SAMPLE_CLIENTS = 5
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Language = Literal["ru", "en"]


class UploadIn(BaseModel):
    file_name: Annotated[str, Field(min_length=1, max_length=200)]
    content: Base64Bytes


class ChoicesIn(BaseModel):
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
    """A sample, a check or an import running in the background, and the report of a
    check or an import once it is done."""

    id: uuid.UUID
    kind: import_jobs.Kind
    file_name: str
    upload_id: uuid.UUID | None
    status: import_jobs.Status
    rows_done: int
    rows_total: int
    report: ReportOut | None
    error: str | None


class SheetOut(BaseModel):
    title: str
    rows: int


class UploadOut(BaseModel):
    """The file waiting on the server, and how far the specialist has come with it."""

    id: uuid.UUID
    file_name: str
    size: int
    sheets: list[SheetOut]
    clients: int
    sampled: bool
    checked: bool
    report: ReportOut | None


class FieldOut(BaseModel):
    key: str
    # The format's header in the language.
    label: str
    type: Literal["text", "date", "money", "number", "bool", "secret"]
    value: str | bool


class RecordOut(BaseModel):
    change: Literal["new", "updated"] | None
    fields: list[FieldOut]
    # A case's payments, expenses, queue, applications, bookings and accounts.
    sections: list["SectionOut"]


class SectionOut(BaseModel):
    sheet: str
    title: str
    records: list[RecordOut]


RecordOut.model_rebuild()


class CardOut(BaseModel):
    """A client as the CRM will have them once the file is loaded."""

    n: int
    status: Literal["new", "updated", "same"]
    # None: the specialist's own accounts.
    name: str | None
    phone: str | None
    sections: list[SectionOut]
    issues: list[IssueOut]


class CardSummaryOut(BaseModel):
    n: int
    status: Literal["new", "updated", "same"]
    name: str | None
    phone: str | None
    consultations: int
    cases: int
    issues: int


class PromptOut(BaseModel):
    text: str


# --- Helpers --------------------------------------------------------------------------


def _read(content: bytes) -> Workbook:
    try:
        return read_workbook(content)
    except WorkbookError as error:
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code in ("file_too_large", "too_many_rows")
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, error.code) from error


def _upload(upload_id: uuid.UUID, user: CurrentUser) -> Upload:
    """The workspace's file waiting on the server; another workspace's, or one the server
    lost in a restart or forgot, is not found."""
    assert user.tenant_id is not None
    upload = import_uploads.get(upload_id, user.tenant_id)
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import_upload_not_found")
    return upload


def _checked(upload: Upload) -> list[Any]:
    if upload.cards is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import_not_checked")
    return upload.cards


async def _import(
    book: Workbook,
    file_name: str,
    choices: dict[str, dict[str, str]],
    user: CurrentUser,
    session: AsyncSession,
    *,
    save: bool,
    progress: Progress,
) -> tuple[ImportRun, dict[str, Any], WorkbookImport]:
    """Run the import in the session; the caller commits it or lets it roll back."""
    assert user.tenant_id is not None
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    run = ImportRun(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        kind=ImportKind.WORKBOOK,
        file_name=file_name,
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
        choices=choices,
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
    return run, report, engine


def _report_out(run: ImportRun, report: dict[str, Any], *, saved: bool) -> ReportOut:
    return ReportOut(
        id=run.id if saved else None,
        kind=run.kind,
        file_name=run.file_name,
        created_at=run.created_at if saved else None,
        **report,
    )


def _start(
    upload: Upload,
    user: CurrentUser,
    kind: import_jobs.Kind,
    choices: dict[str, dict[str, str]],
) -> JobOut:
    """Run the sample, the check or the import of the file in the background, in a session
    of its own: the request's closes when it answers. The sample and the check keep their
    cards with the file and roll back; the import commits and forgets the file."""
    assert user.tenant_id is not None
    tenant_id = user.tenant_id

    async def work(progress: Progress) -> ReportOut | None:
        book = await asyncio.to_thread(read_workbook, upload.content)
        if kind == "sample":
            book = first_clients(book, SAMPLE_CLIENTS)
        async with get_sessionmaker()() as session:
            scope_to_tenant(session, tenant_id)
            run, report, engine = await _import(
                book,
                upload.file_name,
                choices,
                user,
                session,
                save=kind == "import",
                progress=progress,
            )
            if kind == "import":
                await session.commit()
                await session.refresh(run)
                import_uploads.drop(upload)
                return _report_out(run, report, saved=True)
            found = await cards(engine)
            out = _report_out(run, report, saved=False)
            await session.rollback()
        if kind == "sample":
            upload.sample = found
            return None
        upload.cards, upload.report = found, out
        return out

    try:
        job = import_jobs.start(
            tenant_id, kind=kind, file_name=upload.file_name, work=work, upload_id=upload.id
        )
    except ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    if kind == "check":
        # The cards of an older check no longer hold until this one is done.
        upload.cards = upload.report = None
    return _job_out(job)


def _job_out(job: ImportJob) -> JobOut:
    p = job.progress
    return JobOut(
        id=job.id,
        kind=job.kind,
        file_name=job.file_name,
        upload_id=job.upload_id,
        status=job.status,
        rows_done=p.rows_done,
        rows_total=p.rows_total,
        report=job.result,
        error=job.error,
    )


def _upload_out(upload: Upload) -> UploadOut:
    return UploadOut(
        id=upload.id,
        file_name=upload.file_name,
        size=len(upload.content),
        sheets=[SheetOut(title=title, rows=rows) for title, rows in upload.sheets],
        clients=upload.clients,
        sampled=upload.sample is not None,
        checked=upload.cards is not None,
        report=upload.report,
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


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_file(body: UploadIn, user: Specialist) -> UploadOut:
    """Take the workbook to wait on the server in place of the one waiting; nothing goes
    into the CRM yet. A file that cannot be read is refused at once."""
    assert user.tenant_id is not None
    try:
        import_jobs.stop(user.tenant_id)
    except ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    book = await asyncio.to_thread(_read, body.content)
    clients = book.sheets.get("clients")
    upload = import_uploads.put(
        user.tenant_id,
        file_name=body.file_name,
        content=body.content,
        sheets=[(sheet.title, len(sheet.rows)) for sheet in book.sheets.values()],
        clients=len(clients.rows) if clients else 0,
    )
    return _upload_out(upload)


@router.get("/uploads/current")
async def current_upload(user: Specialist) -> UploadOut | None:
    """The file waiting: the page opened again goes on where it was."""
    assert user.tenant_id is not None
    upload = import_uploads.current(user.tenant_id)
    return _upload_out(upload) if upload else None


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload(upload_id: uuid.UUID, user: Specialist) -> Response:
    """«Отменить»: the file and its cards are forgotten, the CRM is as it was."""
    assert user.tenant_id is not None
    upload = _upload(upload_id, user)
    try:
        import_jobs.stop(user.tenant_id)
    except ImportRunning as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_running") from error
    import_uploads.drop(upload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/uploads/{upload_id}/sample", status_code=status.HTTP_202_ACCEPTED)
async def start_sample(upload_id: uuid.UUID, user: Specialist) -> JobOut:
    """Start making the cards of the file's first clients, without writing anything."""
    return _start(_upload(upload_id, user), user, "sample", {})


@router.get("/uploads/{upload_id}/sample")
async def sample_cards(
    upload_id: uuid.UUID, user: Specialist, language: Annotated[Language, Query()] = "ru"
) -> list[CardOut]:
    upload = _upload(upload_id, user)
    if upload.sample is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return [CardOut(n=n, **render(card, language)) for n, card in enumerate(upload.sample)]


@router.post("/uploads/{upload_id}/check", status_code=status.HTTP_202_ACCEPTED)
async def start_check(upload_id: uuid.UUID, body: ChoicesIn, user: Specialist) -> JobOut:
    """Start finding what loading the whole file would do, and every card it would make,
    without writing anything."""
    return _start(_upload(upload_id, user), user, "check", body.choices)


@router.get("/uploads/{upload_id}/cards")
async def list_cards(upload_id: uuid.UUID, user: Specialist) -> list[CardSummaryOut]:
    """The cards of the check, a line each: the specialist opens any of them."""
    found = _checked(_upload(upload_id, user))
    return [CardSummaryOut(**summary(card, n)) for n, card in enumerate(found)]


@router.get("/uploads/{upload_id}/cards/{n}")
async def get_card(
    upload_id: uuid.UUID,
    n: int,
    user: Specialist,
    language: Annotated[Language, Query()] = "ru",
) -> CardOut:
    """One card of the check by its place in the list: no client's data in the address."""
    found = _checked(_upload(upload_id, user))
    if not 0 <= n < len(found):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return CardOut(n=n, **render(found[n], language))


@router.post("/uploads/{upload_id}/import", status_code=status.HTTP_202_ACCEPTED)
async def start_import(upload_id: uuid.UUID, body: ChoicesIn, user: Specialist) -> JobOut:
    """Load the checked file into the CRM: the specialist has looked through its cards."""
    upload = _upload(upload_id, user)
    if upload.cards is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_not_checked")
    return _start(upload, user, "import", body.choices)


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
