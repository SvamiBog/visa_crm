"""Импорт таблиц (backlog 1.14, 1.16): the specialist uploads a spreadsheet, maps its
columns, checks what would happen and imports it. The file is not kept: it is sent again for the
check and for the import. Only the workspace owner imports.

The engine is app/core/importer.py; the rules are in docs/decisions.md → «Импорт таблиц».
"""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Base64Bytes, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    TenantSession,
    effective_modules,
    require_module,
    require_specialist,
    workspace_today,
)
from app.core.audit import record
from app.core.import_fields import (
    BY_KEY,
    LOOKUPS,
    ImportField,
    ValueType,
    fields_for,
    guess,
    kind_modules,
)
from app.core.import_files import FileError, Table, read_table
from app.core.import_values import (
    cell_text,
    looks_like_credentials,
    normalize,
    split_credentials,
    table_year,
)
from app.core.importer import Importer, Options
from app.core.money import Currency
from app.models import (
    AuditAction,
    Country,
    ImportKind,
    ImportMapping,
    ImportRun,
    Tenant,
    User,
    VaultSystem,
)

router = APIRouter(
    prefix="/imports", tags=["imports"], dependencies=[Depends(require_module("clients"))]
)
Specialist = Annotated[CurrentUser, Depends(require_specialist)]

# Where the first specialist's clients apply: the default for new cases.
DEFAULT_SUBMISSION = "BY"
SAMPLES = 3
# A sample of a column with passwords is not shown back: by its header, by its field, or
# by the value — a login with a password after it.
SECRET_HINTS = ("парол", "password", "pass", "кабинет", "логин", "login")
MASK = "••••••"


class FileIn(BaseModel):
    kind: ImportKind
    file_name: Annotated[str, Field(min_length=1, max_length=200)]
    content: Base64Bytes
    sheet: Annotated[str, Field(max_length=100)] | None = None
    header_row: Annotated[int, Field(ge=1, le=20)] | None = None


class OptionsIn(BaseModel):
    currency: Currency | None = None
    submission_country_id: uuid.UUID | None = None
    # The table's country: for a queue or windowed applications.
    country_id: uuid.UUID | None = None
    email_system_id: uuid.UUID | None = None
    site_system_id: uuid.UUID | None = None


class RunIn(FileIn):
    # Column index → field key; a column left out is not imported.
    columns: Annotated[dict[int, str], Field(max_length=100)]
    options: OptionsIn = OptionsIn()
    # Unknown list values: type → normalized value → an id, "new" or "skip".
    values: Annotated[dict[str, dict[str, str]], Field(max_length=10)] = {}


class ColumnOut(BaseModel):
    index: int
    header: str
    samples: list[str]
    field: str | None


class FieldOut(BaseModel):
    key: str
    type: ValueType
    required: bool


class SystemOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str


class OptionsOut(BaseModel):
    currency: str
    submission_country_id: uuid.UUID | None
    country_id: uuid.UUID | None
    email_system_id: uuid.UUID | None
    site_system_id: uuid.UUID | None


class PreviewOut(BaseModel):
    kind: ImportKind
    file_name: str
    sheets: list[str]
    sheet: str
    header_row: int
    rows: int
    columns: list[ColumnOut]
    fields: list[FieldOut]
    options: OptionsOut
    values: dict[str, dict[str, str]]
    systems: list[SystemOut]


class IssueOut(BaseModel):
    row: int
    reason: str
    column: str | None = None


class UnknownOut(BaseModel):
    key: str
    value: str
    rows: int


class ReportOut(BaseModel):
    id: uuid.UUID | None
    kind: ImportKind
    file_name: str
    sheet: str | None
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
    sheet: str | None
    rows: int
    imported: int
    skipped_count: int
    warnings_count: int
    created_by: str | None
    created_at: datetime


# --- Helpers --------------------------------------------------------------------------


def _table(body: FileIn) -> Table:
    try:
        return read_table(body.content, body.sheet, body.header_row)
    except FileError as error:
        status_code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code in ("file_too_large", "too_many_rows")
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code, error.code) from error


async def _modules(session: AsyncSession, user: CurrentUser, kind: ImportKind) -> list[str]:
    modules = await effective_modules(session, user)
    if any(m not in modules for m in kind_modules(kind)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "module_disabled")
    return modules


def _column_keys(headers: list[str]) -> list[str]:
    """How a column is remembered: its normalized header, «#2» for a repeated one, «#1»
    — its place — for one without a header (the names in the queues of Poland, Latvia)."""
    seen: dict[str, int] = {}
    keys = []
    for index, header in enumerate(headers):
        key = normalize(header)
        if not key:
            keys.append(f"#{index + 1}")
            continue
        seen[key] = seen.get(key, 0) + 1
        keys.append(key if seen[key] == 1 else f"{key}#{seen[key]}")
    return keys


def _sheet_name(table: Table, file_name: str) -> str:
    """Where the rows come from: the sheet, or the CSV file without its extension and
    the « (1)» a browser adds to a file downloaded again."""
    if table.sheet:
        return table.sheet
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return re.sub(r"\s*\(\d+\)$", "", stem)


def _sample(value: str, secret: bool) -> str:
    if secret:
        return MASK
    if looks_like_credentials(value):
        login = split_credentials(value)[0]
        return f"{login[:40]} {MASK}" if "@" in login else MASK
    return value[:60]


async def _mapping(session: AsyncSession, kind: ImportKind) -> ImportMapping | None:
    return await session.scalar(select(ImportMapping).where(ImportMapping.kind == kind))


async def _country_by_code(session: AsyncSession, code: str) -> uuid.UUID | None:
    return await session.scalar(select(Country.id).where(Country.code == code))


async def _country_in_name(session: AsyncSession, *names: str) -> uuid.UUID | None:
    """The table's country from its sheet or file name: «Польша с 08.06» → Poland."""
    words = {w for name in names for w in normalize(name).split()}
    rows = await session.execute(select(Country.id, Country.name_ru, Country.name_en))
    for country_id, name_ru, name_en in rows:
        if normalize(name_ru) in words or normalize(name_en) in words:
            return country_id
    return None


async def _systems(session: AsyncSession) -> list[VaultSystem]:
    return list(
        await session.scalars(
            select(VaultSystem)
            .where(VaultSystem.is_active)
            .order_by(VaultSystem.sort_order, VaultSystem.name)
        )
    )


def _check_columns(body: RunIn, table: Table, fields: list[ImportField]) -> None:
    allowed = {f.key for f in fields}
    used: set[str] = set()
    for index, key in body.columns.items():
        if not 0 <= index < len(table.headers) or key not in allowed:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_column")
        if key in used:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_field")
        used.add(key)
    if any(body.kind in f.required_in and f.key not in used for f in fields):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "required_field")
    for kind, choices in body.values.items():
        if kind not in {t.value for t in LOOKUPS} or len(choices) > 1000:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_values")
        if any(len(choice) > 300 for choice in choices.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_values")


async def _options(session: AsyncSession, body: RunIn, tenant: Tenant, used: set[str]) -> Options:
    o = body.options
    submission = o.submission_country_id or await _country_by_code(session, DEFAULT_SUBMISSION)
    if submission is None or await session.get(Country, submission) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_options")
    if body.kind in (ImportKind.QUEUE, ImportKind.ATTEMPTS):
        if o.country_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "country_required")
        if o.country_id == submission or await session.get(Country, o.country_id) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_options")
    systems = {s.id for s in await _systems(session)}
    slots = {
        "email": ({"email_login", "own_email_login", "email_password"}, o.email_system_id),
        "site": ({"site_login", "site_password"}, o.site_system_id),
    }
    for fields, system_id in slots.values():
        if used & fields and system_id not in systems:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "system_required")
    return Options(
        currency=o.currency or tenant.base_currency,
        submission_country_id=submission,
        country_id=o.country_id if body.kind in (ImportKind.QUEUE, ImportKind.ATTEMPTS) else None,
        email_system_id=o.email_system_id,
        site_system_id=o.site_system_id,
    )


async def _import(
    body: RunIn, user: CurrentUser, session: AsyncSession, *, save: bool
) -> tuple[ImportRun, dict[str, Any]]:
    """Run the import in the session; the caller commits it or lets it roll back."""
    assert user.tenant_id is not None
    modules = await _modules(session, user, body.kind)
    table = _table(body)
    fields = fields_for(body.kind, modules)
    _check_columns(body, table, fields)
    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    options = await _options(session, body, tenant, set(body.columns.values()))

    run = ImportRun(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        kind=body.kind,
        file_name=body.file_name,
        sheet=table.sheet or None,
        rows=len(table.rows),
        report={},
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    importer = Importer(
        session,
        tenant_id=user.tenant_id,
        user_id=user.id,
        base_currency=tenant.base_currency,
        kind=body.kind,
        headers=table.headers,
        columns=body.columns,
        options=options,
        values=body.values,
        today=await workspace_today(session, user.tenant_id),
        sheet=_sheet_name(table, body.file_name),
        year=table_year(table.sheet, body.file_name),
        vault="vault" in modules,
    )
    importer.run_id = run.id
    report = (await importer.run(table.rows)).as_json()
    run.report = report
    # Placeholder rows of zeros and dashes are not counted.
    run.rows = report["rows"]

    if save:
        mapping = await _mapping(session, body.kind)
        if mapping is None:
            mapping = ImportMapping(tenant_id=user.tenant_id, kind=body.kind)
            session.add(mapping)
        keys = _column_keys(table.headers)
        # Columns left unmapped are remembered too: next time they stay unmapped.
        columns = dict(mapping.columns or {})
        columns |= {keys[i]: body.columns.get(i, "") for i in range(len(keys)) if keys[i]}
        mapping.columns = columns
        mapping.options = body.options.model_dump(mode="json", exclude={"country_id"})
        values = {k: dict(v) for k, v in (mapping.values or {}).items()}
        for kind, choices in body.values.items():
            values.setdefault(kind, {}).update(choices)
        mapping.values = values
        record(
            session,
            AuditAction.IMPORT_COMPLETED,
            actor=user,
            target_type="import",
            target_id=run.id,
            details={
                "kind": body.kind.value,
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
        sheet=run.sheet,
        created_at=run.created_at if saved else None,
        **report,
    )


# --- Endpoints ------------------------------------------------------------------------


@router.post("/preview")
async def preview(body: FileIn, user: Specialist, session: TenantSession) -> PreviewOut:
    """The sheet's columns with a few values and a field for each: remembered from the
    last import of this kind, else guessed by the header."""
    assert user.tenant_id is not None
    modules = await _modules(session, user, body.kind)
    table = _table(body)
    fields = fields_for(body.kind, modules)
    mapping = await _mapping(session, body.kind)
    remembered = mapping.columns if mapping else {}
    allowed = {f.key for f in fields}
    guessed = guess(table.headers, fields)
    keys = _column_keys(table.headers)

    columns: list[ColumnOut] = []
    taken: set[str] = set()
    for index, header in enumerate(table.headers):
        field = remembered.get(keys[index]) if keys[index] in remembered else guessed[index]
        if not field or field not in allowed or field in taken:
            field = None
        if field:
            taken.add(field)
        secret = (field and BY_KEY[field].type == ValueType.SECRET) or any(
            hint in normalize(header) for hint in SECRET_HINTS
        )
        samples: list[str] = []
        for row in table.rows:
            if len(samples) == SAMPLES:
                break
            value = cell_text(row.cells[index])
            if value is not None:
                samples.append(_sample(value, bool(secret)))
        columns.append(ColumnOut(index=index, header=header, samples=samples, field=field))

    tenant = await session.get(Tenant, user.tenant_id)
    assert tenant is not None
    stored = mapping.options if mapping else {}
    systems = await _systems(session) if "vault" in modules else []
    system_ids = {s.id for s in systems}

    def remembered_system(name: str, kind: str) -> uuid.UUID | None:
        value = stored.get(name)
        if value and uuid.UUID(value) in system_ids:
            return uuid.UUID(value)
        return next((s.id for s in systems if s.kind == kind), None)

    submission = stored.get("submission_country_id")
    return PreviewOut(
        kind=body.kind,
        file_name=body.file_name,
        sheets=table.sheets,
        sheet=table.sheet,
        header_row=table.header_row,
        rows=len(table.rows),
        columns=columns,
        fields=[
            FieldOut(key=f.key, type=f.type, required=body.kind in f.required_in) for f in fields
        ],
        options=OptionsOut(
            currency=stored.get("currency") or tenant.base_currency,
            submission_country_id=uuid.UUID(submission)
            if submission
            else await _country_by_code(session, DEFAULT_SUBMISSION),
            country_id=await _country_in_name(session, table.sheet, body.file_name)
            if body.kind in (ImportKind.QUEUE, ImportKind.ATTEMPTS)
            else None,
            email_system_id=remembered_system("email_system_id", "email"),
            site_system_id=remembered_system("site_system_id", "visa_center"),
        ),
        values=mapping.values if mapping else {},
        systems=[SystemOut(id=s.id, name=s.name, kind=s.kind.value) for s in systems],
    )


@router.post("/check")
async def check(body: RunIn, user: Specialist, session: TenantSession) -> ReportOut:
    """What the import would do, without writing anything."""
    run, report = await _import(body, user, session, save=False)
    out = _report_out(run, report, saved=False)
    await session.rollback()
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def run_import(body: RunIn, user: Specialist, session: TenantSession) -> ReportOut:
    run, report = await _import(body, user, session, save=True)
    await session.commit()
    await session.refresh(run)
    return _report_out(run, report, saved=True)


@router.get("")
async def list_runs(user: Specialist, session: TenantSession) -> list[RunSummary]:
    rows = await session.execute(
        select(ImportRun, User.email)
        .outerjoin(User, User.id == ImportRun.created_by)
        .order_by(ImportRun.created_at.desc())
        .limit(50)
    )
    return [
        RunSummary(
            id=run.id,
            kind=run.kind,
            file_name=run.file_name,
            sheet=run.sheet,
            rows=run.rows,
            imported=run.report.get("imported", 0),
            skipped_count=run.report.get("skipped_count", 0),
            warnings_count=run.report.get("warnings_count", 0),
            created_by=email,
            created_at=run.created_at,
        )
        for run, email in rows
    ]


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, user: Specialist, session: TenantSession) -> ReportOut:
    run = await session.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _report_out(run, run.report, saved=True)


@router.delete("/memory/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_mapping(kind: ImportKind, user: Specialist, session: TenantSession) -> Response:
    """Forget the remembered mapping of a kind: the next upload is guessed afresh."""
    mapping = await _mapping(session, kind)
    if mapping is not None:
        await session.delete(mapping)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
