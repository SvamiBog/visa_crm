"""Rules for consent and contract files (app/core/documents.py) and the file storage
(app/core/storage.py), without the database."""

import io
import uuid

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from cryptography.exceptions import InvalidTag

from app.core import storage
from app.core.config import get_settings
from app.core.documents import (
    MAX_BYTES,
    DocumentError,
    clean_file_name,
    content_disposition,
    content_type_of,
    object_key,
    open_document,
    seal_document,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (PDF, "application/pdf"),
        (b"\xef\xbb\xbf" + PDF, "application/pdf"),  # a few bytes before the header
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (WEBP, "image/webp"),
        (HEIC, "image/heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00", "image/heif"),
    ],
)
def test_type_comes_from_the_first_bytes(data: bytes, content_type: str):
    assert content_type_of(data) == content_type


@pytest.mark.parametrize(
    "data",
    [
        b"<html><script>alert(1)</script></html>",
        b"PK\x03\x04\x14\x00\x06\x00",  # DOCX, XLSX and any other zip
        b"MZ\x90\x00\x03\x00",  # a Windows program
        b"\x00\x00\x00\x18ftypisom",  # an MP4 video
        b"plain text, not a document",
    ],
)
def test_anything_else_is_refused(data: bytes):
    with pytest.raises(DocumentError) as raised:
        content_type_of(data)
    assert raised.value.code == "document_type_not_allowed"


def test_empty_and_too_large_files_are_refused():
    with pytest.raises(DocumentError) as empty:
        content_type_of(b"")
    assert empty.value.code == "file_empty"
    assert content_type_of(PDF + b"\0" * (MAX_BYTES - len(PDF))) == "application/pdf"
    with pytest.raises(DocumentError) as large:
        content_type_of(PDF + b"\0" * (MAX_BYTES - len(PDF) + 1))
    assert large.value.code == "document_too_large"


@pytest.mark.parametrize(
    ("name", "content_type", "cleaned"),
    [
        ("Договор Ковалёва.pdf", "application/pdf", "Договор Ковалёва.pdf"),
        ("C:\\Users\\anna\\scan.PDF", "application/pdf", "scan.PDF"),
        ("../../etc/passwd", "application/pdf", "passwd.pdf"),
        ("photo.jpeg", "image/jpeg", "photo.jpeg"),
        ("photo.png", "image/jpeg", "photo.png.jpg"),  # the name follows the content
        ("согласие\n\t.jpg", "image/jpeg", "согласие.jpg"),
        ("IMG_0001.HEIC", "image/heif", "IMG_0001.HEIC"),
        ("...", "image/png", "document.png"),
        ("", "application/pdf", "document.pdf"),
    ],
)
def test_file_name_is_cleaned(name: str, content_type: str, cleaned: str):
    assert clean_file_name(name, content_type) == cleaned


def test_long_name_keeps_its_extension():
    name = clean_file_name("д" * 300 + ".pdf", "application/pdf")
    assert len(name) == 200 and name.endswith("д.pdf")


def test_download_name_is_utf8_and_always_an_attachment():
    header = content_disposition("Договор №5.pdf")
    assert header.startswith('attachment; filename="document.pdf"; ')
    assert header.endswith(
        "filename*=UTF-8''%D0%94%D0%BE%D0%B3%D0%BE%D0%B2%D0%BE%D1%80%20%E2%84%965.pdf"
    )


def test_object_key_has_ids_only():
    tenant, document = uuid.uuid4(), uuid.uuid4()
    assert object_key(tenant, document) == f"documents/{tenant}/{document}"


def test_a_sealed_file_opens_only_for_its_row():
    key, tenant, document = bytes(range(32)), uuid.uuid4(), uuid.uuid4()
    blob = seal_document(key, tenant, document, PDF)
    assert PDF not in blob
    assert open_document(key, tenant, document, blob) == PDF
    with pytest.raises(InvalidTag):
        open_document(key, tenant, uuid.uuid4(), blob)  # copied onto another row
    with pytest.raises(InvalidTag):
        open_document(key, uuid.uuid4(), document, blob)  # into another workspace
    with pytest.raises(InvalidTag):
        open_document(bytes(32), tenant, document, blob)  # with another workspace's key


# --- Storage ---------------------------------------------------------------------------


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch):
    """Set variables for one test; settings are read again before and after it."""

    def set_vars(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_vars
    monkeypatch.undo()
    get_settings.cache_clear()


S3 = {
    "S3_ENDPOINT": "https://s3.example.test",
    "S3_REGION": "eu-central",
    "S3_BUCKET": "visacrm-files",
    "S3_ACCESS_KEY": "test-access",
    "S3_SECRET_KEY": "test-secret",
}


async def test_tests_keep_files_in_memory():
    await storage.put_object("documents/a/b", b"blob")
    assert storage.MEMORY == {"documents/a/b": b"blob"}
    assert await storage.get_object("documents/a/b") == b"blob"
    await storage.delete_object("documents/a/b")
    with pytest.raises(storage.StorageFailed):
        await storage.get_object("documents/a/b")


async def test_without_a_bucket_other_environments_refuse(environment):
    environment(ENV="production", S3_BUCKET="")
    assert not storage.is_configured()
    with pytest.raises(storage.StorageNotConfigured):
        await storage.put_object("documents/a/b", b"blob")
    with pytest.raises(storage.StorageNotConfigured):
        await storage.get_object("documents/a/b")


async def test_objects_go_to_the_s3_bucket(environment):
    environment(ENV="production", **S3)
    assert storage.is_configured()
    client = storage._s3(get_settings())
    assert client.meta.endpoint_url == "https://s3.example.test"
    assert client.meta.region_name == "eu-central"
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "visacrm-files",
                "Key": "documents/a/b",
                "Body": b"blob",
                "ContentType": "application/octet-stream",
            },
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"blob"), 4)},
            {"Bucket": "visacrm-files", "Key": "documents/a/b"},
        )
        stub.add_response("delete_object", {}, {"Bucket": "visacrm-files", "Key": "documents/a/b"})
        await storage.put_object("documents/a/b", b"blob")
        assert await storage.get_object("documents/a/b") == b"blob"
        await storage.delete_object("documents/a/b")
        stub.assert_no_pending_responses()
    assert storage.MEMORY == {}


async def test_storage_errors_are_reported_without_details(environment):
    environment(ENV="production", **S3)
    client = storage._s3(get_settings())
    with Stubber(client) as stub:
        stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        # The error's name only: no bucket, key or provider message.
        with pytest.raises(storage.StorageFailed, match="^AccessDenied$"):
            await storage.put_object("documents/a/b", b"blob")
        with pytest.raises(storage.StorageFailed):
            await storage.get_object("documents/a/b")
