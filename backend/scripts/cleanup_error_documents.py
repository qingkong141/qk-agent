"""Delete failed document records and their safely scoped upload files."""

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings
from app.db.session import async_session
from app.models.document import Document


async def cleanup(confirm: bool) -> None:
    if not confirm:
        raise SystemExit("Refusing cleanup without --yes")

    upload_root = Path(settings.UPLOAD_DIR).resolve()
    async with async_session() as db:
        failed = list((await db.execute(
            select(Document).where(Document.status == "error")
        )).scalars())
        ready_paths = {
            Path(path).resolve()
            for path in (await db.execute(
                select(Document.file_path).where(Document.status == "ready")
            )).scalars()
        }

        targets: list[Path] = []
        for doc in failed:
            path = Path(doc.file_path).resolve()
            if path.parent != upload_root:
                raise SystemExit(f"Unsafe path outside upload directory: {path}")
            if path in ready_paths:
                raise SystemExit(f"Refusing to delete a file used by a ready document: {path}")
            targets.append(path)

        await db.execute(delete(Document).where(Document.status == "error"))
        await db.commit()
        for path in targets:
            path.unlink(missing_ok=True)

        print(f"Deleted {len(failed)} error records and {len(targets)} upload files")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    asyncio.run(cleanup(confirm=args.yes))


if __name__ == "__main__":
    main()
