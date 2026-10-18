"""Codes in a Word document (backlog 2.1): found and filled however Word split the text."""

import io
import zipfile

import pytest

from app.core.docx_fill import MAX_BYTES, DocxError, codes_in, fill, read_docx

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


def test_a_document_with_a_dtd_is_refused():
    body = runs("x")
    data = make_docx(body)
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    parts["word/document.xml"] = (
        b'<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY e "boom">]>'
        + parts["word/document.xml"].split(b"?>", 1)[1]
    )
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
    bomb = make_docx(runs("x"), extra={"word/media/big.bin": b"\0" * (60 * 1024 * 1024)})
    assert len(bomb) < MAX_BYTES
    with pytest.raises(DocxError) as error:
        read_docx(bomb)
    assert error.value.code == "template_too_large"
