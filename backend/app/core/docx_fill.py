"""Codes in a Word document (DOCX) and filling them with values: the engine of document
templates (backlog 2.1).

A code is text in double curly braces, `{{ФИО}}`, anywhere Word keeps text: the body,
tables, headers, footers, footnotes, text boxes. Word often stores typed text in several
runs (spell check, editing sessions, a change of language), so codes are looked for in a
paragraph's text as a whole; the value takes the formatting of the code's first character.

A code is replaced with plain text and nothing else: no expressions, loops or conditions,
so an uploaded template can never run anything. The file is read as untrusted input: an
archive of limited size, parts without DTDs, no macros. Parts without codes are copied
byte for byte.
"""

import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from lxml import etree

MAX_BYTES = 5 * 1024 * 1024
# What the archive may unpack into, all parts together: a letter with a logo is far less.
MAX_UNPACKED = 50 * 1024 * 1024
MAX_PARTS = 2000

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_T = f"{{{_W}}}t"
_P = f"{{{_W}}}p"
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_WML = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_MAIN = _WML + "document.main+xml"
# The parts where text of the document lives.
_TEXT_PARTS = frozenset(
    _WML + kind
    for kind in ("document.main+xml", "header+xml", "footer+xml", "footnotes+xml", "endnotes+xml")
)

# Between the braces: up to 100 characters, no braces, not only spaces.
CODE = re.compile(r"\{\{([^{}]{1,100})\}\}")
# Characters XML 1.0 does not allow in text (lxml refuses them).
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


class DocxError(ValueError):
    """The file cannot be used as a template; code is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Docx:
    """A Word file taken apart: every part as it was, and the text parts parsed."""

    entries: list[zipfile.ZipInfo]
    data: dict[str, bytes]
    text_parts: dict[str, etree._Element]


def read_docx(data: bytes) -> Docx:
    """The file as a Word document; DocxError if it is not one that can be filled."""
    if not data:
        raise DocxError("file_empty")
    if len(data) > MAX_BYTES:
        raise DocxError("template_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
    except (zipfile.BadZipFile, ValueError) as error:
        raise DocxError("template_not_docx") from error
    with archive:
        if len(entries) > MAX_PARTS or sum(e.file_size for e in entries) > MAX_UNPACKED:
            raise DocxError("template_too_large")
        names = {e.filename for e in entries}
        if "[Content_Types].xml" not in names:
            raise DocxError("template_not_docx")
        try:
            parts = {e.filename: archive.read(e) for e in entries if not e.is_dir()}
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError) as error:
            # Damaged, encrypted or packed with a method zipfile does not know.
            raise DocxError("template_broken") from error
    types = _content_types(parts["[Content_Types].xml"])
    if _MAIN not in types.values():
        # A macro-enabled document or a Word template (.dotx) has a main part of another type.
        raise DocxError("template_not_docx")
    if any(name.lower().endswith("vbaproject.bin") for name in parts):
        raise DocxError("template_has_macros")
    text_parts = {}
    # The body first, then headers, footers and notes, whatever order the package lists.
    for name, content_type in sorted(types.items(), key=lambda item: item[1] != _MAIN):
        if content_type in _TEXT_PARTS and name in parts:
            text_parts[name] = _parse(parts[name])
    return Docx(entries=[e for e in entries if not e.is_dir()], data=parts, text_parts=text_parts)


def codes_in(docx: Docx) -> list[str]:
    """Every code of the document as written between the braces (trimmed), each once, in
    the order they come: the body, then headers, footers and notes."""
    found: dict[str, None] = {}
    for root in docx.text_parts.values():
        for texts in _paragraphs(root):
            for match in CODE.finditer("".join(t.text or "" for t in texts)):
                code = match.group(1).strip()
                if code:
                    found.setdefault(code, None)
    return list(found)


def fill(docx: Docx, value_of: Callable[[str], str | None]) -> bytes:
    """The document with each code replaced by value_of(code as written, trimmed). A code
    it returns None for stays as it is, so a typo is seen in the file."""
    changed: dict[str, bytes] = {}
    for name, root in docx.text_parts.items():
        if any([_fill_paragraph(texts, value_of) for texts in _paragraphs(root)]):
            tree = root.getroottree()
            changed[name] = etree.tostring(
                tree,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=tree.docinfo.standalone,
            )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in docx.entries:
            info = zipfile.ZipInfo(entry.filename, date_time=entry.date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, changed.get(entry.filename, docx.data[entry.filename]))
    return out.getvalue()


def _content_types(data: bytes) -> dict[str, str]:
    """Part name (without the leading slash) → its declared content type."""
    root = _parse(data)
    return {
        override.get("PartName", "").lstrip("/"): override.get("ContentType", "")
        for override in root.iter(f"{_CT}Override")
    }


def _parse(data: bytes) -> etree._Element:
    # Word never writes a DTD; one in a part is either damage or an attack (entities).
    if b"<!DOCTYPE" in data[:4096].upper():
        raise DocxError("template_broken")
    try:
        return etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as error:
        raise DocxError("template_broken") from error


def _paragraphs(root: etree._Element) -> list[list[etree._Element]]:
    """The text elements of each paragraph, in order. A paragraph inside a text box is a
    paragraph of its own, not a part of the one around the box."""
    groups: dict[etree._Element, list[etree._Element]] = {}
    for text in root.iter(_T):
        paragraph = next(text.iterancestors(_P), None)
        if paragraph is not None:
            groups.setdefault(paragraph, []).append(text)
    return list(groups.values())


def _fill_paragraph(texts: list[etree._Element], value_of: Callable[[str], str | None]) -> bool:
    """Replaces the codes of one paragraph in place; True if anything changed.

    A code may begin in one text element and end in another: the value goes where the
    code begins, and the rest of the code is cut from the elements it spans. Matches are
    handled from the last, so the offsets of earlier ones stay valid."""
    pieces = [t.text or "" for t in texts]
    starts, offset = [], 0
    for piece in pieces:
        starts.append(offset)
        offset += len(piece)
    full = "".join(pieces)

    def element_at(position: int) -> int:
        index = 0
        while index + 1 < len(starts) and starts[index + 1] <= position:
            index += 1
        return index

    changed: set[int] = set()
    for match in reversed(list(CODE.finditer(full))):
        code = match.group(1).strip()
        value = value_of(code) if code else None
        if value is None:
            continue
        value = _NOT_XML.sub("", value)
        first, last = element_at(match.start()), element_at(match.end() - 1)
        head = pieces[first][: match.start() - starts[first]]
        tail = pieces[last][match.end() - starts[last] :]
        if first == last:
            pieces[first] = head + value + tail
        else:
            pieces[first] = head + value
            for index in range(first + 1, last):
                pieces[index] = ""
            pieces[last] = tail
        changed.update(range(first, last + 1))
    for index in changed:
        texts[index].text = pieces[index]
        # Word drops leading and trailing spaces of a text element unless told to keep them.
        texts[index].set(_SPACE, "preserve")
    return bool(changed)
