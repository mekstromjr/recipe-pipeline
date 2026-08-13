"""Garage S3 access for the `recipes` bucket.

boto3 is synchronous; handlers call these methods via ``asyncio.to_thread``. Objects here are
small (page JPEGs, source PDFs), so a thread hop per call is fine and keeps the dependency
surface plain boto3 — no aioboto3.

Key layout (shared with the worker API and the backfill importer):
    jobs/<job_id>/source/<original filename>     original upload (permanent record)
    jobs/<job_id>/pages/page-<n>.jpg             upright page images for review/display
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from recipe_pipeline_server.config import Settings


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=BotoConfig(
                # Garage is path-style only; virtual-host style would resolve a bogus vhost.
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        )

    def check(self) -> None:
        """Raise if the bucket is unreachable (readiness probe)."""
        self._client.head_bucket(Bucket=self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra: dict[str, Any] = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def get_bytes(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.ClientError:
            return False
        return True

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def source_key(job_id: str, filename: str) -> str:
    return f"jobs/{job_id}/source/{filename}"


def page_key(job_id: str, page: int) -> str:
    return f"jobs/{job_id}/pages/page-{page}.jpg"
