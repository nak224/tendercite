import hashlib
import uuid
from pathlib import Path, PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from tendercite.api.dependencies import get_repository, get_retrieval
from tendercite.core.config import settings
from tendercite.domain.models import ChunkRead, DocumentRead, PageRead
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.chunking import chunk_pages
from tendercite.services.pdf_parser import PdfParseError, PyPdfParser

router = APIRouter(prefix="/documents", tags=["documents"])
_parser = PyPdfParser()


def _safe_filename(filename: str | None) -> str:
    name = PurePath(filename or "document.pdf").name
    return name or "document.pdf"


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: Annotated[UploadFile, File()],
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> DocumentRead:
    filename = _safe_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    payload = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(payload) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF exceeds configured upload limit")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Uploaded file is not a valid PDF header")

    document_id = str(uuid.uuid4())
    sha256 = hashlib.sha256(payload).hexdigest()
    existing = repository.find_document_by_hash(sha256)
    if existing:
        await _index(existing.id)
        return existing
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = settings.upload_dir / f"{document_id}.pdf"
    pdf_path.write_bytes(payload)

    try:
        parsed_pages = _parser.parse(pdf_path)
    except PdfParseError as exc:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    page_models = [
        PageRead(document_id=document_id, page_number=p.page_number, text=p.text)
        for p in parsed_pages
    ]
    raw_chunks = chunk_pages([(p.page_number, p.text) for p in parsed_pages])
    chunk_models = [
        ChunkRead(
            id=str(uuid.uuid4()),
            document_id=document_id,
            page_number=c.page_number,
            ordinal=c.ordinal,
            text=c.text,
            char_start=c.char_start,
            char_end=c.char_end,
        )
        for c in raw_chunks
    ]

    document = repository.save_document(
        document_id=document_id,
        filename=filename,
        media_type=file.content_type or "application/pdf",
        sha256=sha256,
        pages=page_models,
        chunks=chunk_models,
    )

    await _index(document_id)
    return document


async def _index(document_id: str) -> None:
    if settings.retrieval_enabled:
        try:
            await run_in_threadpool(get_retrieval().index_document, document_id)
        except Exception as exc:
            raise HTTPException(
                503, "Document retained; retry upload or indexing after configuring retrieval"
            ) from exc


@router.get("", response_model=list[DocumentRead])
def list_documents(
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> list[DocumentRead]:
    return repository.list_documents()


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: str,
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> DocumentRead:
    document = repository.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}/pages/{page_number}", response_model=PageRead)
def get_page(
    document_id: str,
    page_number: int,
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> PageRead:
    page = repository.get_page(document_id, page_number)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.get("/{document_id}/chunks", response_model=list[ChunkRead])
def list_chunks(
    document_id: str,
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> list[ChunkRead]:
    if repository.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return repository.list_chunks(document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    repository: Annotated[SqliteRepository, Depends(get_repository)],
) -> None:
    if repository.get_document(document_id) is None:
        raise HTTPException(404, "Document not found")
    if settings.retrieval_enabled:
        try:
            get_retrieval().store.delete_document(document_id)
        except Exception as exc:
            raise HTTPException(503, "Vector cleanup failed; document retained") from exc
    deleted = repository.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    Path(settings.upload_dir / f"{document_id}.pdf").unlink(missing_ok=True)
