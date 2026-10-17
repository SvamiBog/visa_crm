"""File storage: any S3-compatible object store in the EU (S3_* variables).

The provider is a setting, not code: Cloudflare R2, Backblaze B2, MinIO and others speak the
same API. Files are encrypted before they get here (app/core/documents.py), and object keys
are made of ids only, so the store holds neither readable content nor names. Without
S3_BUCKET, local development and tests keep objects in MEMORY; any other environment refuses
to store files.
"""

import asyncio
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings


class StorageNotConfigured(RuntimeError):
    """No file storage is set for this environment."""


class StorageFailed(RuntimeError):
    """The storage did not take or give back an object."""


# Local development and tests without S3: objects stay here until the process ends.
MEMORY: dict[str, bytes] = {}

_CONFIG = Config(
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
    # Path-style addressing and checksums only where the API requires them: what every
    # S3-compatible service accepts.
    s3={"addressing_style": "path"},
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.s3_bucket) or settings.env in ("local", "test")


def _s3_settings() -> Settings | None:
    """The S3 settings, or None for the in-memory store."""
    settings = get_settings()
    if settings.s3_bucket:
        return settings
    if settings.env not in ("local", "test"):
        raise StorageNotConfigured()
    return None


@lru_cache
def _client(endpoint: str, region: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=region,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        config=_CONFIG,
    )


def _s3(settings: Settings):
    return _client(
        settings.s3_endpoint, settings.s3_region, settings.s3_access_key, settings.s3_secret_key
    )


def _put(settings: Settings, key: str, data: bytes) -> None:
    try:
        _s3(settings).put_object(
            Bucket=settings.s3_bucket, Key=key, Body=data, ContentType="application/octet-stream"
        )
    except (BotoCoreError, ClientError) as error:
        raise StorageFailed(type(error).__name__) from error


def _get(settings: Settings, key: str) -> bytes:
    try:
        return _s3(settings).get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
    except (BotoCoreError, ClientError) as error:
        raise StorageFailed(type(error).__name__) from error


def _delete(settings: Settings, key: str) -> None:
    try:
        _s3(settings).delete_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as error:
        raise StorageFailed(type(error).__name__) from error


async def put_object(key: str, data: bytes) -> None:
    settings = _s3_settings()
    if settings is None:
        MEMORY[key] = data
        return
    await asyncio.to_thread(_put, settings, key, data)


async def get_object(key: str) -> bytes:
    settings = _s3_settings()
    if settings is None:
        if key not in MEMORY:
            raise StorageFailed("NoSuchKey")
        return MEMORY[key]
    return await asyncio.to_thread(_get, settings, key)


async def delete_object(key: str) -> None:
    settings = _s3_settings()
    if settings is None:
        MEMORY.pop(key, None)
        return
    await asyncio.to_thread(_delete, settings, key)
