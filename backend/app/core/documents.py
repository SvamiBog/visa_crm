"""Client paperwork files (backlog 1.2): the signed consent and the contract.

A file is judged by its first bytes, not by its name or by what the browser says: only PDF
and photos (JPEG, PNG, WebP, HEIC) are taken. Before it goes to the storage
(app/core/storage.py) the file is encrypted with the workspace's key and bound to its row,
like vault passwords (app/core/vault.py): the storage provider sees neither the content nor
whose it is, and a blob copied onto another row does not open.
"""

import unicodedata
import uuid
from urllib.parse import quote

from app.core.crypto import decrypt, encrypt

MAX_BYTES = 10 * 1024 * 1024
MAX_NAME = 200

# Content type → extensions a file of that type may end with; the first one is added
# when the name has none of them.
EXTENSIONS = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
    "image/heic": (".heic",),
    "image/heif": (".heif", ".heic"),
}
# ISO base media file brands of iPhone photos.
_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis"}
_HEIF_BRANDS = {b"mif1", b"msf1"}


class DocumentError(ValueError):
    """The file cannot be taken; code is the API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def content_type_of(data: bytes) -> str:
    """The file's type by its signature; DocumentError for anything else."""
    if not data:
        raise DocumentError("file_empty")
    if len(data) > MAX_BYTES:
        raise DocumentError("document_too_large")
    # A PDF header may follow a few junk bytes (the format allows up to 1024).
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        if data[8:12] in _HEIC_BRANDS:
            return "image/heic"
        if data[8:12] in _HEIF_BRANDS:
            return "image/heif"
    raise DocumentError("document_type_not_allowed")


def clean_file_name(name: str, content_type: str) -> str:
    """The name to show and to download with: no folders or control characters, at most
    MAX_NAME characters, and an extension that matches the content."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip(" .")
    extensions = EXTENSIONS[content_type]
    if not name.lower().endswith(extensions):
        name = (name or "document") + extensions[0]
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        name = stem[: MAX_NAME - len(ext) - 1].rstrip(" .") + dot + ext
    return name


def content_disposition(file_name: str) -> str:
    """Always a download, never shown inline on the app's origin. The name goes in UTF-8
    (RFC 6266); the plain one is for clients that do not read it."""
    ext = file_name.rpartition(".")[2]
    return f"attachment; filename=\"document.{ext}\"; filename*=UTF-8''{quote(file_name)}"


def object_key(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """Ids only: names and client data never reach the storage."""
    return f"documents/{tenant_id}/{document_id}"


def _aad(tenant_id: uuid.UUID, document_id: uuid.UUID) -> bytes:
    return b"document:" + tenant_id.bytes + document_id.bytes


def seal_document(key: bytes, tenant_id: uuid.UUID, document_id: uuid.UUID, data: bytes) -> bytes:
    return encrypt(key, data, _aad(tenant_id, document_id))


def open_document(key: bytes, tenant_id: uuid.UUID, document_id: uuid.UUID, blob: bytes) -> bytes:
    """Raises InvalidTag if the blob is not this row's or was changed in the storage."""
    return decrypt(key, blob, _aad(tenant_id, document_id))
