import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, UploadFile
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models.document import Document
from app.rag.pipeline import rag_pipeline
from app.schemas.document import (
    DocumentResponse,
    DocumentSearchRequest,
    DocumentSearchResult,
    DocumentUploadResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])


async def process_document(document_id: str, file_path: str):
    from app.db.session import async_session

    async with async_session() as db:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return

        try:
            chunk_count = rag_pipeline.ingest_document(
                file_path,
                metadata={
                    "source": doc.filename,
                    "document_id": doc.id,
                    "user_id": doc.user_id,
                },
            )
            doc.status = "ready"
            doc.chunk_count = chunk_count
            logger.info(f"Document {document_id} ingested: {chunk_count} chunks")
        except Exception as e:
            logger.error(f"Document {document_id} processing failed: {e}")
            doc.status = "error"
            doc.chunk_count = 0

        await db.commit()


@router.post("/upload", response_model=DocumentUploadResponse, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
):
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    doc_id = str(uuid.uuid4())
    ext = Path(file.filename or "file").suffix
    save_path = upload_dir / f"{doc_id}{ext}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    doc = Document(
        id=doc_id,
        user_id=user["id"],
        filename=file.filename or "unknown",
        file_path=str(save_path),
        file_type=ext.lstrip("."),
        status="processing",
    )
    db.add(doc)
    await db.commit()

    background_tasks.add_task(process_document, doc_id, str(save_path))
    return DocumentUploadResponse(document_id=doc_id)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(db: DbSession, user: CurrentUser):
    result = await db.execute(
        select(Document).where(Document.user_id == user["id"]).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


@router.post("/search", response_model=list[DocumentSearchResult])
async def search_documents(data: DocumentSearchRequest, user: CurrentUser):
    results = rag_pipeline.search(data.query, user_id=user["id"], top_k=data.top_k)
    return results
