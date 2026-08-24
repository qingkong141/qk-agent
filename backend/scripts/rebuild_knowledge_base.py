"""Destructively rebuild the active knowledge-base collection from source files."""

import argparse
import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import select

from app.db.session import async_session
from app.models.document import Document
from app.rag.pipeline import _collection_name, rag_pipeline


async def rebuild(confirm: bool) -> None:
    if not confirm:
        raise SystemExit("Refusing destructive rebuild without --yes")

    async with async_session() as db:
        documents = list((await db.execute(
            select(Document).where(Document.status == "ready").order_by(Document.created_at)
        )).scalars())

        missing = [doc for doc in documents if not Path(doc.file_path).is_file()]
        if missing:
            missing_ids = ", ".join(doc.id for doc in missing)
            raise SystemExit(f"Source files are missing; collection was not changed: {missing_ids}")

        unique_documents: list[Document] = []
        seen: set[tuple[str, str]] = set()
        for doc in documents:
            digest = hashlib.sha256(Path(doc.file_path).read_bytes()).hexdigest()
            key = (doc.user_id, digest)
            if key in seen:
                continue
            seen.add(key)
            unique_documents.append(doc)

        collection = _collection_name()
        store = rag_pipeline._get_vector_store()
        store.delete_collection()
        rag_pipeline._vector_store = None
        print(f"Deleted collection: {collection}")

        total_chunks = 0
        for doc in unique_documents:
            chunk_count = rag_pipeline.ingest_document(
                doc.file_path,
                metadata={
                    "source": doc.filename,
                    "document_id": doc.id,
                    "user_id": doc.user_id,
                },
            )
            doc.chunk_count = chunk_count
            total_chunks += chunk_count
            print(f"Indexed {doc.id}: {chunk_count} chunks")

        await db.commit()
        print(
            f"Rebuild complete: {len(unique_documents)} unique documents, "
            f"{len(documents) - len(unique_documents)} duplicates skipped, "
            f"{total_chunks} chunks"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="confirm deletion of the active vector collection")
    args = parser.parse_args()
    asyncio.run(rebuild(confirm=args.yes))


if __name__ == "__main__":
    main()
