"""Reading a file in the VisaCRM data format (backlog 1.21, docs/data-format.md): one XLSX
workbook, every sheet at once. The file comes from the user, so it is bounded before it is
read — its size, its unpacked size (an XLSX is a zip), its stylesheet, the rows and
columns. XML is parsed with defusedxml (openpyxl picks it up when installed).

What a file grows into in memory is not bounded by its parts' sizes (backlog S5): openpyxl
builds objects for whole parts, and a member may unpack past the size it declares. So an
uploaded file is parsed in a process of its own under memory and CPU limits (parse_upload):
whatever swells fails there as file_too_large, and the web process, which serves every
workspace, keeps its memory.

Sheets and headers are matched by their Russian or English names; anything else is passed
by with a warning. Each cell is read strictly (app/core/data_values.py). A bad cell in a
required column skips its row, in another column only the cell — but the row keeps its key
in `invalid`, and a bad currency of a payment or an expense skips the row too (a strict
column: the currency of a sum is not guessed); the report names the
sheet, the row and the column, never the value.

A file is read as it is, not as it says it is: the size a sheet declares is not trusted
(some programs write «A1» whatever is on the sheet), and a formula saved without its
result — a file written by a script, never opened in Excel — reads as an empty cell with a
line in the report.
"""

import asyncio
import io
import multiprocessing
import re
import warnings
import zipfile
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO, Any
from xml.etree.ElementTree import ParseError
from xml.parsers.expat import ExpatError
from xml.parsers.expat import errors as expat_errors

from defusedxml.ElementTree import iterparse
from lxml.etree import LxmlError

from app.core.data_format import (
    ABOUT_EXPORTED,
    ABOUT_SHEET,
    CODE_LENGTH,
    REFERENCE_SHEET,
    SHEETS,
    Column,
    ColumnKind,
    Sheet,
    ValueType,
)
from app.core.data_values import (
    Cell,
    CellError,
    cell_text,
    normalize,
    parse_bool,
    parse_choice,
    parse_currency,
    parse_date,
    parse_document_number,
    parse_email,
    parse_int,
    parse_list,
    parse_money,
    parse_percent,
    parse_phone,
    parse_rate,
    parse_telegram,
    parse_text,
)

MAX_BYTES = 10 * 1024 * 1024
MAX_UNPACKED = 120 * 1024 * 1024
# openpyxl reads the stylesheet by this name and builds an object for every entry in it,
# whatever the sheets use; an ordinary one is a few KB.
STYLES = "xl/styles.xml"
MAX_STYLES = 1024 * 1024
# The parser process's address space and CPU time (Linux; a Windows machine has no limits).
# A file of 20,000 rows parses well inside them; keep the memory below the service's limit
# minus the web process.
PARSE_MEMORY = 768 * 1024 * 1024
PARSE_CPU_SECONDS = 60
# Rows of data on all sheets together.
MAX_ROWS = 20_000
MAX_COLUMNS = 100
# Empty rows are passed by, but a sheet is not read without end.
MAX_EMPTY_ROWS = 5000
# A name or a list value: looked up in the CRM, so its length is checked there.
LOOKUP_LENGTH = 200
SECRET_LENGTH = 200
_EXPORTED_TEXT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$")


class _NoResult:
    """A cell with a formula whose result the file does not hold."""


NO_RESULT = _NoResult()
RawCell = Cell | _NoResult


class WorkbookError(ValueError):
    """A file that cannot be imported; the code is the API error (errors.* in locales)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Issue:
    """A line of the report: where and why, without the cell's value."""

    reason: str
    sheet: str
    row: int | None = None
    column: str | None = None


@dataclass
class Row:
    # The row number as the specialist sees it in the file.
    number: int
    # Parsed values of the filled cells, by column key.
    values: dict[str, Any] = field(default_factory=dict)
    # Columns present on the sheet whose cell is empty: a snapshot row clears them.
    blank: set[str] = field(default_factory=set)
    # Optional columns whose filled cell could not be read (the report has the warning):
    # what depends on such a cell is not guessed from the cell being absent.
    invalid: set[str] = field(default_factory=set)


@dataclass
class SheetData:
    spec: Sheet
    # The sheet's name as in the file.
    title: str
    # Column key → its header as in the file, for the report.
    headers: dict[str, str]
    rows: list[Row] = field(default_factory=list)


@dataclass
class Workbook:
    sheets: dict[str, SheetData] = field(default_factory=dict)
    # «О файле» → «Выгружено»: the file is a CRM export (a snapshot), naive local time.
    exported_at: datetime | None = None
    skipped: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)


def first_clients(book: Workbook, count: int) -> Workbook:
    """The rows of a workbook about its first clients: theirs on every sheet and those of
    their cases (backlog 1.22) — a sample to show before the whole file is gone through.
    The clients are those of «Клиенты» in its order, else the ones the other sheets name
    first. The file's own issues are left out: the check of the whole file reports them."""
    chosen: list[str] = []
    for spec in SHEETS:
        if (sheet := book.sheets.get(spec.key)) is None:
            continue
        key = "code" if spec.key == "clients" else "client"
        for row in sheet.rows:
            code = row.values.get(key)
            if len(chosen) < count and code is not None and code not in chosen:
                chosen.append(code)
    clients = set(chosen)
    cases_sheet = book.sheets.get("cases")
    cases = {
        row.values["code"]
        for row in (cases_sheet.rows if cases_sheet else [])
        if row.values.get("client") in clients and "code" in row.values
    }

    def theirs(key: str, row: Row) -> bool:
        if key == "clients":
            return row.values.get("code") in clients
        return row.values.get("client") in clients or row.values.get("case") in cases

    part = Workbook(exported_at=book.exported_at)
    for key, sheet in book.sheets.items():
        rows = [row for row in sheet.rows if theirs(key, row)]
        if rows:
            part.sheets[key] = SheetData(sheet.spec, sheet.title, dict(sheet.headers), rows)
    return part


_BY_NAME = {normalize(name): sheet for sheet in SHEETS for name in (sheet.ru, sheet.en)}
_RUSSIAN = {normalize(sheet.ru) for sheet in SHEETS}
_PASSED_BY = {normalize(name) for name in REFERENCE_SHEET}
_ABOUT = {normalize(name) for name in ABOUT_SHEET}
_EXPORTED = {normalize(name) for name in ABOUT_EXPORTED}


def read_workbook(data: bytes) -> Workbook:
    """Every sheet of the format in the file, parsed; raises WorkbookError for a file that
    cannot be read at all."""
    if len(data) > MAX_BYTES:
        raise WorkbookError("file_too_large")
    result = Workbook()
    counter = _RowCounter()
    for title, rows in _xlsx_sheets(data):
        name = normalize(title)
        if name in _ABOUT:
            result.exported_at = _exported_at(rows)
            continue
        if name in _PASSED_BY:
            continue
        spec = _BY_NAME.get(name)
        if spec is None:
            result.warnings.append(Issue("unknown_sheet", title))
            continue
        if spec.key in result.sheets:
            result.warnings.append(Issue("duplicate_sheet", title))
            continue
        sheet = _read_sheet(spec, title, name in _RUSSIAN, rows, result, counter)
        if sheet is not None:
            result.sheets[spec.key] = sheet
    return result


_parser: ProcessPoolExecutor | None = None


def _limit_parser(memory: int, cpu_seconds: int) -> None:
    """The parser process's initializer: past the memory, an allocation fails (MemoryError);
    past the CPU time, the system stops the process."""
    try:
        import resource  # POSIX only: the service runs on Linux
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def _pool() -> ProcessPoolExecutor:
    global _parser
    if _parser is None:
        if "forkserver" in multiprocessing.get_all_start_methods():
            context = multiprocessing.get_context("forkserver")
            # The server imports the reader once; each parser is forked from it ready.
            context.set_forkserver_preload([__name__, "openpyxl"])
        else:
            context = multiprocessing.get_context("spawn")
        _parser = ProcessPoolExecutor(
            max_workers=1,  # one parse at a time in the whole server
            mp_context=context,
            initializer=_limit_parser,
            initargs=(PARSE_MEMORY, PARSE_CPU_SECONDS),
            max_tasks_per_child=1,  # a fresh process, with fresh limits, for every file
        )
    return _parser


def stop_parser() -> None:
    """Stop the parser process and wait for it; the next file starts one with the limits as
    they are then."""
    global _parser
    if _parser is not None:
        pool, _parser = _parser, None
        # Waited for: a pool shut down without waiting may still be replacing the process
        # that has just finished its file (concurrent.futures in Python 3.12).
        pool.shutdown(wait=True, cancel_futures=True)


async def parse_upload(data: bytes) -> Workbook:
    """read_workbook in the parser process: an upload, and the file again for each sample,
    check and import run."""
    global _parser
    pool = _pool()
    try:
        return await asyncio.get_running_loop().run_in_executor(pool, read_workbook, data)
    except MemoryError as error:
        raise WorkbookError("file_too_large") from error
    except BrokenProcessPool as error:
        # The system stopped the parser: past its CPU time, or for its memory. A file
        # waiting behind it is refused too; the next one gets a new parser. A broken pool
        # has already stopped its processes, so it is not waited for.
        if _parser is pool:
            _parser = None
        pool.shutdown(wait=False)
        raise WorkbookError("file_too_large") from error


class _RowCounter:
    def __init__(self) -> None:
        self.rows = 0

    def add(self) -> None:
        self.rows += 1
        if self.rows > MAX_ROWS:
            raise WorkbookError("too_many_rows")


@contextmanager
def _reading() -> Iterator[None]:
    """A broken or foreign file makes zipfile and openpyxl raise many kinds of errors: they
    read as unreadable. Running out of the parser's memory reads as too large."""
    try:
        yield
    except WorkbookError:
        raise
    except Exception as error:
        code = "file_too_large" if _out_of_memory(error) else "file_unreadable"
        raise WorkbookError(code) from error


# An XML parser written in C that fails to allocate says so as a parse error with a code of
# its own: libxml2 (lxml, which openpyxl uses for some parts) and expat (ElementTree).
_LIBXML_NO_MEMORY = 2  # XML_ERR_NO_MEMORY
_EXPAT_NO_MEMORY = expat_errors.codes[expat_errors.XML_ERROR_NO_MEMORY]


def _out_of_memory(error: Exception) -> bool:
    if isinstance(error, LxmlError):
        return getattr(error, "code", None) == _LIBXML_NO_MEMORY
    if isinstance(error, ParseError | ExpatError):
        return getattr(error, "code", None) == _EXPAT_NO_MEMORY
    return isinstance(error, MemoryError)


def _xlsx_sheets(data: bytes) -> Iterator[tuple[str, Iterator[tuple[RawCell, ...]]]]:
    import openpyxl  # imported here: only uploads need it

    with _reading():
        archive = zipfile.ZipFile(io.BytesIO(data))
    with archive:
        members = archive.infolist()
        if sum(info.file_size for info in members) > MAX_UNPACKED or any(
            info.filename == STYLES and info.file_size > MAX_STYLES for info in members
        ):
            raise WorkbookError("file_too_large")
        with _reading(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Loaded once, with the saved results; formulas are told from empty cells by
            # the sheet's XML (_rows).
            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                # Read to the last cell that is there, not to the size the sheet declares.
                sheet.reset_dimensions()
                yield sheet.title, _rows(sheet, archive)
        finally:
            book.close()


def _rows(sheet: Any, archive: zipfile.ZipFile) -> Iterator[tuple[RawCell, ...]]:
    """The sheet's rows, cut to MAX_COLUMNS; the sheets are parsed as they are read, so a
    broken one shows here. A cell with a formula and no saved result is NO_RESULT."""
    values = sheet.iter_rows(values_only=True)
    with _reading():
        # The part openpyxl reads this sheet from: a read-only sheet keeps its path there.
        source = archive.open(sheet._worksheet_path)
    with source:
        formulas = _formula_places(source)
        while True:
            with _reading():
                cells = next(values, None)
                places = next(formulas, frozenset())
            if cells is None:
                return
            cells = cells[:MAX_COLUMNS]
            yield tuple(
                NO_RESULT if cell is None and index in places else cell
                for index, cell in enumerate(cells)
            )


_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_ROW, _FORMULA = _SHEET_NS + "row", _SHEET_NS + "f"


def _formula_places(source: IO[bytes]) -> Iterator[frozenset[int]]:
    """For each row openpyxl gives of a sheet, the places of its cells that hold a formula.
    The sheet's XML is read as a stream beside openpyxl, so rows and cells are numbered as
    openpyxl numbers them (worksheet/_reader.py, worksheet/_read_only.py): a row or a cell
    without its reference follows the one before, a missing row is an empty one, and a row
    that goes back is passed by."""
    from openpyxl.utils.cell import coordinate_to_tuple

    given = number = 0
    for _, element in iterparse(source):
        if element.tag != _ROW:
            continue
        number = _row_number(element.get("r"), number)
        places, column = set(), 0
        for cell in element:
            reference = cell.get("r")
            column = coordinate_to_tuple(reference)[1] if reference else column + 1
            if cell.find(_FORMULA) is not None:
                places.add(column - 1)
        element.clear()
        while given < number - 1:
            given += 1
            yield frozenset()
        if given < number:
            given += 1
            yield frozenset(places)


def _row_number(reference: str | None, previous: int) -> int:
    if reference is None:
        return previous + 1
    try:
        return int(reference)
    except ValueError:
        number = float(reference)
        if not number.is_integer():
            raise
        return int(number)


def _header(column: Column, russian: bool) -> str:
    return column.ru if russian else column.en


def _read_sheet(
    spec: Sheet,
    title: str,
    russian: bool,
    rows: Iterator[tuple[RawCell, ...]],
    result: Workbook,
    counter: _RowCounter,
) -> SheetData | None:
    first = next(rows, None)
    if first is None:
        return None
    header_row = _values(first)
    by_header = {normalize(name): c for c in spec.columns for name in (c.ru, c.en)}
    places: dict[str, int] = {}
    headers: dict[str, str] = {}
    for index, cell in enumerate(header_row):
        text = cell_text(cell)
        if text is None:
            continue
        column = by_header.get(normalize(text))
        if column is None:
            result.warnings.append(Issue("unknown_column", title, column=text))
        elif column.key in places:
            result.warnings.append(Issue("duplicate_column", title, column=text))
        else:
            places[column.key] = index
            headers[column.key] = text
    missing = [c for c in spec.columns if c.required and c.key not in places]
    if missing:
        for column in missing:
            result.skipped.append(Issue("missing_column", title, column=_header(column, russian)))
        return None

    sheet = SheetData(spec, title, headers)
    codes: set[str] = set()
    empty = 0
    for number, raw in enumerate(rows, start=2):
        cells = _values(raw)
        unsaved = {index for index, cell in enumerate(raw) if cell is NO_RESULT}
        if not unsaved and all(cell_text(cell) is None for cell in cells):
            empty += 1
            if empty > MAX_EMPTY_ROWS:
                break
            continue
        empty = 0
        counter.add()
        row = _read_row(spec, title, number, cells, unsaved, places, headers, result)
        if row is None:
            continue
        code = row.values.get("code")
        if code is not None:
            if code in codes:
                result.skipped.append(
                    Issue("duplicate_code", title, number, headers[spec.code.key])
                )
                continue
            codes.add(code)
        sheet.rows.append(row)
    return sheet


def _read_row(
    spec: Sheet,
    title: str,
    number: int,
    cells: tuple[Cell, ...],
    unsaved: set[int],
    places: dict[str, int],
    headers: dict[str, str],
    result: Workbook,
) -> Row | None:
    row = Row(number)
    for column in spec.columns:
        if column.key not in places:
            continue
        index = places[column.key]
        cell = cells[index] if index < len(cells) else None
        if column.kind == ColumnKind.REF:
            if column.key == "client_name" and (text := cell_text(cell)):
                row.values["client_name"] = " ".join(text.split())
            continue
        if index in unsaved:
            # Not an empty cell: a snapshot row does not clear its field.
            issue = Issue("formula", title, number, headers[column.key])
            if column.required or column.strict:
                result.skipped.append(issue)
                return None
            result.warnings.append(issue)
            row.invalid.add(column.key)
            continue
        if cell_text(cell) is None:
            if column.required:
                result.skipped.append(Issue("required", title, number, headers[column.key]))
                return None
            row.blank.add(column.key)
            continue
        try:
            row.values[column.key] = _parse(column, cell)
        except CellError as error:
            issue = Issue(error.reason, title, number, headers[column.key])
            if column.required or column.strict:
                result.skipped.append(issue)
                return None
            result.warnings.append(issue)
            row.invalid.add(column.key)
    return row


def _values(cells: tuple[RawCell, ...]) -> tuple[Cell, ...]:
    return tuple(None if isinstance(cell, _NoResult) else cell for cell in cells)


def _parse(column: Column, cell: Cell) -> Any:
    match column.type:
        case ValueType.CODE:
            return parse_text(cell, column.max_length or CODE_LENGTH)
        case ValueType.TEXT:
            return parse_text(cell, column.max_length)
        case ValueType.DOCUMENT_NUMBER:
            return parse_document_number(cell)
        case ValueType.DATE:
            return parse_date(cell)
        case ValueType.INT:
            return parse_int(cell)
        case ValueType.MONEY:
            return parse_money(cell)
        case ValueType.RATE:
            return parse_rate(cell)
        case ValueType.PERCENT:
            return parse_percent(cell)
        case ValueType.CURRENCY:
            return parse_currency(cell)
        case ValueType.BOOL:
            return parse_bool(cell)
        case ValueType.PHONE:
            return parse_phone(cell)
        case ValueType.TELEGRAM:
            return parse_telegram(cell)
        case ValueType.EMAIL:
            return parse_email(cell)
        case ValueType.CHOICE:
            return parse_choice(cell, column.choices)
        case ValueType.COUNTRIES:
            return parse_list(cell)
        case ValueType.SECRET:
            # As typed: a password is not tidied up.
            text = cell if isinstance(cell, str) else cell_text(cell) or ""
            if len(text) > (column.max_length or SECRET_LENGTH):
                raise CellError("too_long")
            return text
    # Countries, cities and the specialist's lists: looked up by the import.
    return parse_text(cell, LOOKUP_LENGTH)


def _exported_at(rows: Iterator[tuple[RawCell, ...]]) -> datetime | None:
    for cells in rows:
        if len(cells) < 2 or normalize(cell_text(cells[0]) or "") not in _EXPORTED:
            continue
        value = cells[1]
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and (match := _EXPORTED_TEXT.match(value.strip())):
            day, month, year, hour, minute = (int(g) for g in match.groups())
            try:
                return datetime(year, month, day, hour, minute)
            except ValueError:
                return None
    return None
