"""Codes in a Word document (backlog 2.1): found and filled however Word split the text.
A template is untrusted input: what it may cost to read and fill is bounded (backlog S4)."""

import io
import struct
import time
import zipfile
import zlib

import pytest

from app.core.docx_fill import (
    MAX_BYTES,
    MAX_CODES,
    MAX_NODES,
    DocxError,
    codes_in,
    fill,
    read_docx,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MACROS = "application/vnd.ms-word.document.macroEnabled.main+xml"
DOCUMENT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
HEADER = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"


def make_docx(
    body: str,
    *,
    header: str | None = None,
    main_type: str = DOCUMENT,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    """A minimal Word file: the body's paragraphs as given, in Word's own markup."""
    overrides = f'<Override PartName="/word/document.xml" ContentType="{main_type}"/>'
    if header is not None:
        # Listed before the body, as Word may do: codes still come in the body's order.
        overrides = f'<Override PartName="/word/header1.xml" ContentType="{HEADER}"/>' + overrides
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{overrides}</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}" xmlns:w14="http://schemas.microsoft.com/office/word/2010/'
        'wordml" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'mc:Ignorable="w14"><w:body>{body}</w:body></w:document>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        if header is not None:
            archive.writestr(
                "word/header1.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:hdr xmlns:w="{W}">{header}</w:hdr>',
            )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


def runs(*texts: str, bold_first: bool = False) -> str:
    """A paragraph of runs, the way Word stores text typed in several goes."""
    parts = []
    for i, text in enumerate(texts):
        props = "<w:rPr><w:b/></w:rPr>" if bold_first and i == 0 else ""
        parts.append(f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>')
    return f"<w:p>{''.join(parts)}</w:p>"


def text_of(data: bytes, part: str = "word/document.xml") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(part).decode()


def paragraphs(data: bytes, part: str = "word/document.xml") -> list[str]:
    """The text of each paragraph, as Word shows it."""
    from lxml import etree

    root = etree.fromstring(text_of(data, part).encode())
    return ["".join(t.text or "" for t in p.iter(f"{{{W}}}t")) for p in root.iter(f"{{{W}}}p")]


def text_elements(data: bytes) -> list[str]:
    """The text of each text element of the body, in order: where each value went."""
    from lxml import etree

    root = etree.fromstring(text_of(data).encode())
    return [t.text or "" for t in root.iter(f"{{{W}}}t")]


def with_part(
    data: bytes, name: str, content: bytes, compress_type: int = zipfile.ZIP_DEFLATED
) -> bytes:
    """The archive with one more member, packed as asked."""
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr(name, content, compress_type=compress_type)
    return out.getvalue()


def lying_about_size(real: int, declared: int = 16) -> bytes:
    """A template with a member of `real` zero bytes whose headers say it has `declared`
    (with the checksum of that many zero bytes): a few KB that would unpack into much."""
    data = make_docx(runs("{{ФИО}}"), extra={"customXml/pad.bin": b"\0" * real})
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo("customXml/pad.bin")
    honest = struct.pack("<III", info.CRC, info.compress_size, info.file_size)
    lying = struct.pack("<III", zlib.crc32(b"\0" * declared), info.compress_size, declared)
    assert data.count(honest) == 2  # the local header and the central directory
    return data.replace(honest, lying)


def declaring_too_much() -> bytes:
    """A small archive whose members say they unpack into far more than a template may."""
    return make_docx(runs("x"), extra={"word/media/big.bin": b"\0" * (60 * 1024 * 1024)})


VALUES = {"фио": "Иван Тестов", "дата рождения": "05.10.1990", "full name": "Ivan Testov"}


def value_of(code: str) -> str | None:
    return VALUES.get(code.casefold())


def test_codes_are_found_across_runs_and_parts():
    data = make_docx(
        runs("Я, ", "{{", "ФИО", "}}", ", родился {{дата ", "рождения}}.")
        + "<w:tbl><w:tr><w:tc>"
        + runs("{{Full name}}")
        + "</w:tc></w:tr></w:tbl>",
        header=runs("{{ФИО}} — {{Номер паспорта}}"),
    )
    # In order, each once, as written (between the braces, trimmed).
    assert codes_in(read_docx(data)) == ["ФИО", "дата рождения", "Full name", "Номер паспорта"]


def test_values_replace_codes_keeping_the_rest():
    data = make_docx(
        runs("Я, ", "{{", "ФИО", "}}", ", родился {{дата ", "рождения}}.", bold_first=True),
        header=runs("{{ФИО}}: {{неизвестный}}"),
    )
    filled = fill(read_docx(data), value_of)
    assert paragraphs(filled) == ["Я, Иван Тестов, родился 05.10.1990."]
    # An unknown code stays as written, so the specialist sees it in the file.
    assert paragraphs(filled, "word/header1.xml") == ["Иван Тестов: {{неизвестный}}"]
    # The rest of the package is untouched.
    with zipfile.ZipFile(io.BytesIO(filled)) as archive:
        assert archive.namelist() == [
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/header1.xml",
        ]
    assert 'mc:Ignorable="w14"' in text_of(filled)  # namespaces Word relies on are kept


def test_value_takes_the_formatting_of_the_code():
    data = make_docx(runs("{{ФИО}}", " и текст", bold_first=True))
    from lxml import etree

    root = etree.fromstring(text_of(fill(read_docx(data), value_of)).encode())
    first_run = root.find(f".//{{{W}}}r")
    assert first_run.find(f"{{{W}}}rPr/{{{W}}}b") is not None
    assert first_run.find(f"{{{W}}}t").text == "Иван Тестов"


def test_values_are_text_not_markup():
    data = make_docx(runs("{{ФИО}}"))

    def tricky(code: str) -> str:
        return 'A & B <w:t>"x"</w:t>\x00\x07 end'

    filled = fill(read_docx(data), tricky)
    assert paragraphs(filled) == ['A & B <w:t>"x"</w:t> end']


def test_leading_and_trailing_spaces_survive():
    data = make_docx("<w:p><w:r><w:t>{{ФИО}}</w:t></w:r><w:r><w:t>!</w:t></w:r></w:p>")
    filled = fill(read_docx(data), lambda code: "  два пробела ")
    assert paragraphs(filled) == ["  два пробела !"]
    assert 'xml:space="preserve"' in text_of(filled)


def test_braces_without_a_code_are_left_alone():
    data = make_docx(runs("{{}} {{ }} {не код} {{слишком", "}}"))
    assert codes_in(read_docx(data)) == ["слишком"]
    assert paragraphs(fill(read_docx(data), lambda code: "X")) == ["{{}} {{ }} {не код} X"]


def test_an_unchanged_document_keeps_its_parts_byte_for_byte():
    data = make_docx(runs("Без кодов"))
    filled = fill(read_docx(data), value_of)
    assert text_of(filled) == text_of(data)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "file_empty"),
        (b"%PDF-1.7 not a word file", "template_not_docx"),
        (b"PK\x03\x04 broken zip", "template_not_docx"),
        (
            make_docx(runs("x"), main_type=MACROS),
            "template_not_docx",
        ),
        (make_docx(runs("x"), extra={"word/vbaProject.bin": b"macro"}), "template_has_macros"),
        (
            make_docx(
                runs("x"),
                extra={"word/header9.xml": b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'},
            ),
            None,  # not one of the parts filled: kept as it is
        ),
    ],
)
def test_only_plain_word_files_are_taken(data: bytes, code: str | None):
    if code is None:
        read_docx(data)
        return
    with pytest.raises(DocxError) as error:
        read_docx(data)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("encoding", "before"),
    [
        ("UTF-8", ""),
        # Wherever the DTD is in the part, not only at its start, and in any encoding.
        ("UTF-8", "<!--" + "x" * 5000 + "-->"),
        ("UTF-16", ""),
    ],
    ids=["at_the_start", "after_a_long_comment", "utf_16"],
)
def test_a_document_with_a_dtd_is_refused(encoding: str, before: str):
    body = runs("x")
    data = make_docx(body)
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    parts["word/document.xml"] = (
        f'<?xml version="1.0" encoding="{encoding}"?>{before}'
        '<!DOCTYPE w:document [<!ENTITY e "boom">]>'
        + parts["word/document.xml"].decode().split("?>", 1)[1]
    ).encode(encoding)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, part in parts.items():
            archive.writestr(name, part)
    with pytest.raises(DocxError) as error:
        read_docx(out.getvalue())
    assert error.value.code == "template_broken"


def test_size_limits():
    with pytest.raises(DocxError) as error:
        read_docx(b"PK" + b"\0" * MAX_BYTES)
    assert error.value.code == "template_too_large"
    # A small archive that unpacks into far too much: refused before it is read.
    bomb = declaring_too_much()
    assert len(bomb) < MAX_BYTES
    with pytest.raises(DocxError) as error:
        read_docx(bomb)
    assert error.value.code == "template_too_large"


def test_an_ordinary_template_reads_to_its_parts_as_they_are():
    data = make_docx(runs("{{ФИО}}"), header=runs("{{Full name}}"))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert read_docx(data).data == {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def inflated(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The bytes each call of zipfile's decompressor gives while an archive is read."""
    sizes: list[int] = []
    make = zipfile._get_decompressor

    class Counting:
        def __init__(self, inner) -> None:
            self._inner = inner

        def decompress(self, *args: object) -> bytes:
            out = self._inner.decompress(*args)
            sizes.append(len(out))
            return out

        def flush(self, *args: object) -> bytes:
            out = self._inner.flush(*args)
            sizes.append(len(out))
            return out

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def counting(compress_type: int) -> object:
        inner = make(compress_type)
        return None if inner is None else Counting(inner)

    monkeypatch.setattr(zipfile, "_get_decompressor", counting)
    return sizes


def test_a_member_is_never_unpacked_past_its_declared_size(inflated: list[int]):
    # 28 MiB of zeros deflate into a few KB; the headers say the member has 16 bytes.
    data = lying_about_size(real=28 * 1024 * 1024, declared=16)
    assert len(data) < 64 * 1024
    docx = read_docx(data)
    assert docx.data["customXml/pad.bin"] == b"\0" * 16
    assert codes_in(docx) == ["ФИО"]
    # Read in pieces of 64 KiB at most, and stopped at the declared size.
    assert max(inflated) <= 64 * 1024
    assert sum(inflated) < 1024 * 1024


def test_a_member_longer_than_it_says_is_too_large(monkeypatch: pytest.MonkeyPatch):
    """Were zipfile to stop cutting a member at its declared size, the reader still would:
    the file is too large, not broken."""
    read = zipfile.ZipExtFile.read

    def past_the_end(self: zipfile.ZipExtFile, n: int = -1) -> bytes:
        data = read(self, n)
        return data + b"\0" if data else data

    monkeypatch.setattr(zipfile.ZipExtFile, "read", past_the_end)
    with pytest.raises(DocxError) as error:
        read_docx(make_docx(runs("x")))
    assert error.value.code == "template_too_large"


@pytest.mark.parametrize(
    "compress_type", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"]
)
def test_only_stored_and_deflated_members_are_taken(compress_type: int):
    # Word packs parts with deflate (or stores them); other methods are not read at all.
    data = with_part(make_docx(runs("x")), "customXml/pad.bin", b"\0" * 1024, compress_type)
    with pytest.raises(DocxError) as error:
        read_docx(data)
    assert error.value.code == "template_broken"
    stored = with_part(make_docx(runs("x")), "customXml/a.bin", b"a", zipfile.ZIP_STORED)
    assert read_docx(stored).data["customXml/a.bin"] == b"a"


def test_an_archive_with_two_parts_of_one_name_is_refused():
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = with_part(make_docx(runs("{{ФИО}}")), "word/document.xml", b"<x/>")
    with pytest.raises(DocxError) as error:
        read_docx(data)
    assert error.value.code == "template_broken"


@pytest.mark.parametrize(
    ("element", "count"),
    [("<w:t/>", MAX_NODES), ('<w:t w:a="" w:b="" w:c="" w:d=""/>', MAX_NODES // 4)],
    ids=["elements", "attributes"],
)
def test_parts_with_too_many_nodes_are_refused(element: str, count: int):
    # A few KB of zip that would be hundreds of MB once parsed.
    read_docx(make_docx(f"<w:p>{element * (count // 2)}</w:p>"))
    too_many = make_docx(f"<w:p>{element * count}</w:p>")
    assert len(too_many) < 64 * 1024
    with pytest.raises(DocxError) as error:
        read_docx(too_many)
    assert error.value.code == "template_too_large"


def test_a_template_has_a_limited_number_of_codes():
    # Each code written counts, in the body and in headers alike.
    full = make_docx(runs("{{ФИО}} " * (MAX_CODES - 1)), header=runs("{{Full name}}"))
    assert codes_in(read_docx(full)) == ["ФИО", "Full name"]
    over = make_docx(runs("{{ФИО}} " * MAX_CODES), header=runs("{{Full name}}"))
    with pytest.raises(DocxError) as error:
        codes_in(read_docx(over))
    assert error.value.code == "template_too_large"


@pytest.mark.parametrize(
    "body",
    [runs(*["{{ФИО}}"] * 20_000), runs("{{ФИО}}" * 20_000)],
    ids=["one_paragraph_of_runs", "one_text_element"],
)
def test_twenty_thousand_codes_fill_in_well_under_a_second(body: str):
    # A template stored before the limit of codes still fills, in time linear in its codes.
    docx = read_docx(make_docx(body))
    started = time.perf_counter()
    filled = fill(docx, value_of)
    assert time.perf_counter() - started < 1
    assert paragraphs(filled) == ["Иван Тестов" * 20_000]


@pytest.mark.parametrize(
    ("texts", "filled"),
    [
        (["x{{Ф", "ИО}}y", "{{ФИО}}"], ["xИван Тестов", "y", "Иван Тестов"]),
        (["{{Ф", "И", "О}}"], ["Иван Тестов", "", ""]),
        (["{{", "Ф", "", "ИО}}!"], ["Иван Тестов", "", "", "!"]),
        (
            ["{{ФИО}}{{ФИО}}-{{ФИ", "О}}{{нет}}"],
            ["Иван ТестовИван Тестов-Иван Тестов", "{{нет}}"],
        ),
        (["a{{ФИО", "}}b{{дата ", "рождения}}c"], ["aИван Тестов", "b05.10.1990", "c"]),
        (["{{ФИО}}", "", "{{Full name}}"], ["Иван Тестов", "", "Ivan Testov"]),
    ],
    ids=["two_runs", "three_runs", "empty_run", "unknown_after", "two_codes", "empty_between"],
)
def test_each_value_goes_where_its_code_begins(texts: list[str], filled: list[str]):
    data = make_docx(runs(*texts))
    assert text_elements(fill(read_docx(data), value_of)) == filled
