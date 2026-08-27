"""Upload intake (#6): PDFs, single photos, or a ZIP batch -> queued jobs + Garage sources.

Sits on the human (forward-auth) path — the uploader's identity stamps ``uploaded_by``.
Every accepted file becomes ONE job in ``queued``; the worker pull loop (#10) will claim
them. Types are sniffed by magic bytes, never trusted from the filename, and re-uploads of
identical content (same sha256 on a non-rejected job) are reported as duplicates rather
than minting duplicate jobs — so re-sending a whole ZIP after a flaky upload is safe.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import uuid
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from recipe_pipeline_shared.jobs import JobStatus, SourceKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recipe_pipeline_server.api.deps import StorageDep, UserDep
from recipe_pipeline_server.db.models import Asset, Job
from recipe_pipeline_server.db.session import get_session
from recipe_pipeline_server.storage import source_key

logger = logging.getLogger("recipe_service")

router = APIRouter(prefix="/api", tags=["uploads"])

SessionDep = Depends(get_session)

# Caps: a phone photo is ~3-8MB, a multi-page 600dpi scan PDF ~10-20MB. Batch cap bounds
# one request's memory (files are read fully to hash + store them).
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_BATCH_BYTES = 500 * 1024 * 1024
MAX_ZIP_MEMBERS = 300

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"heif", b"mif1"}


def sniff(data: bytes) -> tuple[str, str] | None:
    """(kind, content_type) from magic bytes; kind is 'pdf' | 'image' | 'zip'."""
    if data.startswith(b"%PDF"):
        return "pdf", "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if len(data) > 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "image", "image/heic"
    if data.startswith(b"PK\x03\x04"):
        return "zip", "application/zip"
    return None


class UploadResult(BaseModel):
    filename: str
    status: str  # queued | duplicate | error
    job_id: str | None = None
    detail: str | None = None


def _junk(name: str) -> bool:
    """ZIP noise that should vanish silently rather than surface as errors."""
    parts = PurePosixPath(name).parts
    return any(p == "__MACOSX" or p.startswith(".") for p in parts)


async def _ingest(
    name: str,
    data: bytes,
    kind: str,
    content_type: str,
    *,
    user: str,
    session: AsyncSession,
    storage,
) -> UploadResult:
    """One file -> one queued job with its source object. Independent per file."""
    sha = hashlib.sha256(data).hexdigest()
    dup = (
        await session.execute(
            select(Job).where(Job.source_sha256 == sha, Job.status != JobStatus.REJECTED)
        )
    ).scalar()
    if dup:
        return UploadResult(
            filename=name, status="duplicate",
            detail=f"identical content already uploaded as {dup.source_name} ({dup.status})",
        )
    job = Job(
        # explicit id: the column default fires at FLUSH, so job.id would still be None
        # when the S3 key is built below.
        id=uuid.uuid4(),
        status=JobStatus.QUEUED,
        source_kind=SourceKind.PDF if kind == "pdf" else SourceKind.IMAGE,
        source_name=name,
        uploaded_by=user,
        source_sha256=sha,
    )
    key = source_key(str(job.id), name)
    # S3 first: an orphaned object on a later DB error is harmless; a job row without its
    # source is not.
    await asyncio.to_thread(storage.put_bytes, key, data, content_type)
    job.assets.append(Asset(kind="source", s3_key=key, content_type=content_type))
    session.add(job)
    await session.commit()
    logger.info("upload %s (%s, %d bytes) queued as %s by %s", name, kind, len(data), job.id, user)
    return UploadResult(filename=name, status="queued", job_id=str(job.id))


@router.post("/uploads")
async def upload(
    files: list[UploadFile],
    user: UserDep,
    storage: StorageDep,
    session: AsyncSession = SessionDep,
) -> dict:
    if not files:
        raise HTTPException(400, "no files in request")
    results: list[UploadResult] = []
    total = 0
    for f in files:
        name = PurePosixPath(f.filename or "unnamed").name
        data = await f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            results.append(UploadResult(
                filename=name, status="error",
                detail=f"larger than {MAX_FILE_BYTES // 1024 // 1024}MB"))
            continue
        total += len(data)
        if total > MAX_BATCH_BYTES:
            results.append(UploadResult(filename=name, status="error", detail="batch size cap hit"))
            break
        kindct = sniff(data)
        if kindct is None:
            results.append(UploadResult(
                filename=name, status="error",
                detail="not a PDF, JPEG, PNG, HEIC, or ZIP"))
            continue
        kind, ctype = kindct
        if kind != "zip":
            results.append(await _ingest(name, data, kind, ctype,
                                         user=user, session=session, storage=storage))
            continue
        # --- ZIP batch: each member becomes its own job ---
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            members = [m for m in zf.infolist() if not m.is_dir() and not _junk(m.filename)]
        except zipfile.BadZipFile:
            results.append(UploadResult(filename=name, status="error", detail="corrupt ZIP"))
            continue
        if len(members) > MAX_ZIP_MEMBERS:
            results.append(UploadResult(
                filename=name, status="error",
                detail=f"ZIP has {len(members)} files (cap {MAX_ZIP_MEMBERS})"))
            continue
        for m in members:
            mname = PurePosixPath(m.filename).name
            if m.file_size > MAX_FILE_BYTES:
                results.append(UploadResult(filename=mname, status="error",
                                            detail="member larger than the per-file cap"))
                continue
            total += m.file_size
            if total > MAX_BATCH_BYTES:
                results.append(UploadResult(filename=mname, status="error",
                                            detail="batch size cap hit"))
                break
            mdata = zf.read(m)
            mk = sniff(mdata)
            if mk is None or mk[0] == "zip":  # no nested zips
                results.append(UploadResult(filename=mname, status="error",
                                            detail="not a PDF, JPEG, PNG, or HEIC"))
                continue
            results.append(await _ingest(mname, mdata, mk[0], mk[1],
                                         user=user, session=session, storage=storage))
    return {"results": [r.model_dump() for r in results]}
