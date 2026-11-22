"""An uploaded workbook is parsed in a process of its own, under memory and CPU limits
(backlog S5): whatever part of the file swells, the parse fails there as file_too_large, the
web process that serves every workspace keeps its memory, and the next file is read as
usual. The limits are set on Linux, where the service runs; elsewhere the parser runs
without them."""

import base64
import functools
import io
import struct
import sys
import tracemalloc
import zipfile
import zlib
from collections.abc import Callable, Iterator

import pytest
from httpx import AsyncClient

from app.core import workbook
from app.core.workbook import Workbook, WorkbookError, parse_upload
from tests.helpers import MakeUser, login
from tests.test_workbook_rules import book, repacked, stuffed_styles

MiB = 1024 * 1024
THEME = "xl/theme/theme1.xml"
NORMAL = book({"Клиенты": [["Код клиента", "Имя"], ["K-1", "Анна"]]})
# What the test process may allocate while the parser works: the file sent and the answer.
CALLER_MEMORY = 8 * MiB

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="the parser's limits are set on Linux only"
)


@functools.cache
def stuffed_sheet_rels(links: int) -> bytes:
    """A sheet's relationships with that many links: openpyxl reads them whole, whatever the
    sheet uses, and builds an object for each."""
    link = (
        '<Relationship Id="rId{}" Target="https://example.com/" TargetMode="External" Type="'
        'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"/>'
    )
    xml = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(link.format(n) for n in range(links))
        + "</Relationships>"
    )
    return repacked(NORMAL, {"xl/worksheets/_rels/sheet1.xml.rels": xml.encode()})


@functools.cache
def strings_among_sheets(strings: int) -> bytes:
    """A shared-strings table under a sheet's name: openpyxl finds it by the content type the
    file gives and keeps every string, so a limit on parts outside xl/worksheets/ misses it."""
    table = (
        b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + b"<si><t>abcdefgh</t></si>" * strings
        + b"</sst>"
    )
    override = (
        b'<Override PartName="/xl/worksheets/strings.xml" ContentType="application/'
        b'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
    )
    return repacked(
        NORMAL,
        {
            "xl/worksheets/strings.xml": table,
            "[Content_Types].xml": lambda xml: xml.replace(b"</Types>", override + b"</Types>"),
        },
    )


@functools.cache
def swelling_theme(real: int, declared: int = 16) -> bytes:
    """The theme, which openpyxl reads whole, really `real` bytes while its headers say
    `declared`: zipfile unpacks it in one piece past the size the unpacked-size limit counts."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(NORMAL)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            if info.filename != THEME:
                target.writestr(info, source.read(info.filename))
        with target.open(THEME, "w") as theme:
            for _ in range(real // MiB):
                theme.write(b" " * MiB)
    data = out.getvalue()
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo(THEME)
    honest = struct.pack("<III", info.CRC, info.compress_size, info.file_size)
    lying = struct.pack("<III", zlib.crc32(b" " * declared), info.compress_size, declared)
    assert data.count(honest) == 2  # the local header and the central directory
    return data.replace(honest, lying)


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """The parser started afresh with the limits a test gives, and stopped after it."""

    def start(*, memory: int = workbook.PARSE_MEMORY, cpu_seconds: int = 60) -> None:
        workbook.stop_parser()
        monkeypatch.setattr(workbook, "PARSE_MEMORY", memory)
        monkeypatch.setattr(workbook, "PARSE_CPU_SECONDS", cpu_seconds)

    yield start
    workbook.stop_parser()


async def parsed_apart(data: bytes) -> tuple[Workbook | WorkbookError, int]:
    """What the parse of the file gave, and the most the test process allocated meanwhile."""
    tracemalloc.start()
    try:
        try:
            result: Workbook | WorkbookError = await parse_upload(data)
        except WorkbookError as error:
            result = error
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def clients_of(result: Workbook | WorkbookError) -> list[dict]:
    assert isinstance(result, Workbook), result
    return [row.values for row in result.sheets["clients"].rows]


async def test_an_upload_is_parsed_outside_the_web_process():
    """The rels part swells about twentyfold in openpyxl; none of it is in the caller."""
    result, peak = await parsed_apart(stuffed_sheet_rels(60_000))

    assert clients_of(result) == [{"code": "K-1", "first_name": "Анна"}]
    assert peak < CALLER_MEMORY


SWOLLEN = {
    "sheet rels": lambda: stuffed_sheet_rels(200_000),
    "strings named as a sheet": lambda: strings_among_sheets(3_000_000),
    "a member past its declared size": lambda: swelling_theme(384 * MiB),
}


@linux_only
@pytest.mark.parametrize("swollen", SWOLLEN)
async def test_a_part_that_swells_past_the_parse_memory_is_refused(
    limits: Callable[..., None], swollen: str
):
    limits(memory=256 * MiB)
    data = SWOLLEN[swollen]()
    assert len(data) <= workbook.MAX_BYTES

    result, peak = await parsed_apart(data)

    assert isinstance(result, WorkbookError) and result.code == "file_too_large", result
    assert peak < CALLER_MEMORY
    # The next file is read as usual, under the same limits.
    assert clients_of(await parse_upload(NORMAL)) == [{"code": "K-1", "first_name": "Анна"}]


@linux_only
async def test_a_parse_past_its_cpu_time_is_refused_and_the_next_file_is_read(
    limits: Callable[..., None],
):
    """The parser is stopped by the system; a new one reads the next file."""
    limits(cpu_seconds=1)

    with pytest.raises(WorkbookError) as error:
        await parse_upload(strings_among_sheets(3_000_000))

    assert error.value.code == "file_too_large"
    assert clients_of(await parse_upload(NORMAL)) == [{"code": "K-1", "first_name": "Анна"}]


async def test_an_upload_with_a_swollen_stylesheet_is_refused(
    client: AsyncClient, make_user: MakeUser
):
    await make_user("spec@example.com")
    await login(client, "spec@example.com")
    data = stuffed_styles(NORMAL, 40_000)

    response = await client.post(
        "/api/imports/uploads",
        json={"file_name": "styles.xlsx", "content": base64.b64encode(data).decode()},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "file_too_large"
