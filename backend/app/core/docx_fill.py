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

What a template may cost is bounded whatever it claims about itself (backlog S4): a part
is unpacked no further than its declared size, the parts parsed hold a limited number of
XML nodes, a template has a limited number of codes, and filling takes time linear in its
codes. The functions are synchronous: the API runs them in a worker thread.
"""

import bisect
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
# XML nodes of the parts that are parsed, all together: elements, attributes, comments.
MAX_NODES = 300_000
# Codes in a template, each time one is written.
MAX_CODES = 1000

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

# Parts are unpacked and parsed in pieces of this size.
_CHUNK = 64 * 1024
# How Word packs parts; a member packed otherwise is not unpacked at all.
_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
# A parser of its own for each part: a parser is not to be shared between threads.
_PARSER_OPTIONS = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
    "huge_tree": False,
}


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
        # The declared sizes: no member is unpacked past its own (_read_member).
        if len(entries) > MAX_PARTS or sum(e.file_size for e in entries) > MAX_UNPACKED:
            raise DocxError("template_too_large")
        names = {e.filename for e in entries}
        if "[Content_Types].xml" not in names:
            raise DocxError("template_not_docx")
        if len(names) < len(entries):
            # Two members of one name: which of them a program reads is up to the program.
            raise DocxError("template_broken")
        try:
            parts = {e.filename: _read_member(archive, e) for e in entries if not e.is_dir()}
        except DocxError:
            raise  # a ValueError too, and not to be taken for damage
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError) as error:
            # Damaged or encrypted.
            raise DocxError("template_broken") from error
    nodes = _Nodes()
    types = _content_types(parts["[Content_Types].xml"], nodes)
    if _MAIN not in types.values():
        # A macro-enabled document or a Word template (.dotx) has a main part of another type.
        raise DocxError("template_not_docx")
    if any(name.lower().endswith("vbaproject.bin") for name in parts):
        raise DocxError("template_has_macros")
    text_parts = {}
    # The body first, then headers, footers and notes, whatever order the package lists.
    for name, content_type in sorted(types.items(), key=lambda item: item[1] != _MAIN):
        if content_type in _TEXT_PARTS and name in parts:
            text_parts[name] = _parse(parts[name], nodes)
    return Docx(entries=[e for e in entries if not e.is_dir()], data=parts, text_parts=text_parts)


def codes_in(docx: Docx) -> list[str]:
    """Every code of the document as written between the braces (trimmed), each once, in
    the order they come: the body, then headers, footers and notes. DocxError past
    MAX_CODES codes written: no letter needs that many, and each is work on every fill."""
    found: dict[str, None] = {}
    written = 0
    for root in docx.text_parts.values():
        for texts in _paragraphs(root):
            for match in CODE.finditer("".join(t.text or "" for t in texts)):
                code = match.group(1).strip()
                if code:
                    written += 1
                    if written > MAX_CODES:
                        raise DocxError("template_too_large")
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


def _read_member(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    """A member's bytes. Read whole, a member that lies about its size would be unpacked
    at once into as much as a GiB; read in pieces, zipfile unpacks no more than a piece at
    a time and stops at the declared size, so a member costs what it declares."""
    if entry.compress_type not in _METHODS:
        raise DocxError("template_broken")
    chunks, size = [], 0
    with archive.open(entry) as member:
        while chunk := member.read(_CHUNK):
            size += len(chunk)
            # zipfile cuts at the declared size; should it ever stop doing so, this does.
            if size > entry.file_size:
                raise DocxError("template_too_large")
            chunks.append(chunk)
    return b"".join(chunks)


class _Nodes:
    """XML nodes the parts of one template may still have (MAX_NODES): an element of a few
    bytes takes a hundred and more once parsed, so bytes alone do not bound the memory."""

    def __init__(self) -> None:
        self.left = MAX_NODES

    def take(self, count: int) -> None:
        self.left -= count
        if self.left < 0:
            raise DocxError("template_too_large")


def _content_types(data: bytes, nodes: _Nodes) -> dict[str, str]:
    """Part name (without the leading slash) → its declared content type."""
    root = _parse(data, nodes)
    return {
        override.get("PartName", "").lstrip("/"): override.get("ContentType", "")
        for override in root.iter(f"{_CT}Override")
    }


def _parse(data: bytes, nodes: _Nodes) -> etree._Element:
    """A part as a tree. Fed to the parser in pieces and counted as it goes, so a part
    with too many nodes is refused before it is all in memory."""
    parser = etree.XMLPullParser(events=("start", "comment", "pi"), **_PARSER_OPTIONS)
    root_seen = False
    try:
        for at in range(0, len(data), _CHUNK):
            parser.feed(data[at : at + _CHUNK])
            for event, node in parser.read_events():
                if event != "start":
                    nodes.take(1)
                    continue
                if not root_seen:
                    # Word never writes a DTD; one is damage or an attack (entities). It
                    # comes before the root element, so it is known by now.
                    if node.getroottree().docinfo.doctype:
                        raise DocxError("template_broken")
                    root_seen = True
                nodes.take(1 + len(node.attrib))
        return parser.close()
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
    code begins, and the rest of the code is cut from the elements it spans. The cuts of
    each element are gathered first and the element is rebuilt once, so the time is
    linear in the paragraph's text and codes, however many there are."""
    pieces = [t.text or "" for t in texts]
    starts, offset = [], 0
    for piece in pieces:
        starts.append(offset)
        offset += len(piece)
    # Element → what to cut from its text and put in its place, in order.
    cuts: dict[int, list[tuple[int, int, str]]] = {}
    for match in CODE.finditer("".join(pieces)):
        code = match.group(1).strip()
        value = value_of(code) if code else None
        if value is None:
            continue
        value = _NOT_XML.sub("", value)
        start, end = match.span()
        # The element a character is in: the last one that starts at or before it.
        first = bisect.bisect_right(starts, start) - 1
        last = bisect.bisect_right(starts, end - 1) - 1
        for index in range(first, last + 1):
            at = starts[index]
            cut_from, cut_to = max(start, at) - at, min(end, at + len(pieces[index])) - at
            cuts.setdefault(index, []).append((cut_from, cut_to, value if index == first else ""))
    for index, element_cuts in cuts.items():
        text, kept, done = pieces[index], [], 0
        for cut_from, cut_to, put in element_cuts:
            kept += (text[done:cut_from], put)
            done = cut_to
        kept.append(text[done:])
        texts[index].text = "".join(kept)
        # Word drops leading and trailing spaces of a text element unless told to keep them.
        texts[index].set(_SPACE, "preserve")
    return bool(cuts)
