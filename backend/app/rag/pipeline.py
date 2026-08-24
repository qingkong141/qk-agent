import hashlib
import re
import threading
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.tools import tool
from loguru import logger

from app.config import settings
from app.llm.embeddings_factory import _resolve_provider, create_embeddings


def _collection_name() -> str:
    provider = _resolve_provider()
    model = settings.EMBEDDING_MODEL.replace(":", "_").replace("/", "_")
    return f"knowledge_base_{provider}_{model}"

SOURCE_PATTERN = re.compile(
    r"\[来源:\s*(?P<source>[^\]|]+)\s*\|\s*相关度:\s*(?P<score>[\d.]+)\]\n(?P<content>.*?)(?=\n\n---|\Z)",
    re.DOTALL,
)


def parse_knowledge_sources(text: str) -> list[dict]:
    return [
        {
            "source": m.group("source").strip(),
            "score": float(m.group("score")),
            "content": m.group("content").strip()[:200],
        }
        for m in SOURCE_PATTERN.finditer(text)
    ]


def format_knowledge_results(results: list[dict]) -> str:
    if not results:
        return "知识库中未找到相关信息。"
    return "\n\n---\n".join(
        f"[来源: {r['source']} | 相关度: {r['score']}]\n{r['content']}"
        for r in results
    )


KB_MISS_TEXT = "知识库中未找到相关信息。"

_QUERY_STOP_WORDS = frozenset({
    "什么", "怎么", "如何", "哪些", "介绍", "一下", "请问", "是什么", "有多少",
    "为什么", "能否", "可以", "告诉", "讲讲", "说说", "关于",
})


def _extract_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for block in re.findall(r"[\u4e00-\u9fff]+", query):
        for i in range(max(len(block) - 1, 0)):
            if i + 2 <= len(block):
                bigram = block[i : i + 2]
                if bigram not in _QUERY_STOP_WORDS:
                    terms.append(bigram)
        for size in (3, 4):
            for i in range(len(block) - size + 1):
                term = block[i : i + size]
                if term not in _QUERY_STOP_WORDS:
                    terms.append(term)
    terms.extend(re.findall(r"[a-zA-Z]{4,}", query.lower()))
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique.append(term)
    return unique


def _term_in_text(term: str, text: str) -> bool:
    """3 字及以上直接子串匹配；2 字词同样子串匹配（「力学」不会命中「用力」）"""
    return len(term) >= 2 and term in text


def _keyword_overlap(query: str, results: list[dict]) -> bool:
    terms = _extract_query_terms(query)
    if not terms:
        return False
    corpus = " ".join(r["content"] for r in results[:3])
    matches = sum(1 for term in terms if _term_in_text(term, corpus))
    if matches >= 2:
        return True
    bigrams = [t for t in terms if len(t) == 2]
    return any(_term_in_text(t, corpus) for t in bigrams)


def filter_results_by_terms(query: str, results: list[dict]) -> list[dict]:
    terms = _extract_query_terms(query)
    if not terms:
        return []
    filtered = [r for r in results if any(_term_in_text(t, r["content"]) for t in terms)]
    return filtered


def has_relevant_hit(query: str, results: list[dict]) -> bool:
    """向量分高但主题不符时（如量子力学 vs 医疗 PDF），用关键词二次校验"""
    if not results:
        return False
    best = max(r["score"] for r in results)
    if best < settings.RAG_SIMILARITY_THRESHOLD:
        return False
    if best >= settings.RAG_HIGH_CONFIDENCE_SCORE:
        return True
    return _keyword_overlap(query, results)


def is_kb_miss(text: str) -> bool:
    if not text or text.strip() in ("无", ""):
        return True
    return text.strip() == KB_MISS_TEXT


def _sync_db_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _is_postgres() -> bool:
    return settings.DATABASE_URL.startswith("postgresql")


def _table_to_markdown(table: list[list[object | None]]) -> str:
    rows = [
        [str(cell).strip() if cell is not None else "" for cell in row]
        for row in table
        if row and any(cell not in (None, "") for cell in row)
    ]
    if not rows:
        return ""

    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * max_cols
    body = normalized[1:]

    def render_row(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("\n", " ") for cell in row) + " |"

    return "\n".join([render_row(header), render_row(separator), *(render_row(row) for row in body)])


def _native_text_quality(text: str) -> tuple[int, float]:
    compact = "".join(text.split())
    if not compact:
        return 0, 0.0
    readable = sum(
        char.isalnum() or "\u4e00" <= char <= "\u9fff" or char in "，。！？；：,.!?;:（）()《》、-_/"
        for char in compact
    )
    return len(compact), readable / len(compact)


def _needs_ocr(text: str, has_images: bool = False) -> bool:
    length, readable_ratio = _native_text_quality(text)
    return (
        length == 0
        or readable_ratio < settings.OCR_MIN_READABLE_RATIO
        or (has_images and length < settings.OCR_MIN_TEXT_LENGTH)
    )


def _ocr_result_data(result) -> dict:
    if isinstance(result, dict):
        return result
    data = getattr(result, "json", None)
    if callable(data):
        data = data()
    if isinstance(data, str):
        import json
        data = json.loads(data)
    if isinstance(data, dict):
        return data.get("res", data)
    data = getattr(result, "res", None)
    return data if isinstance(data, dict) else {}


class PpOcrEngine:
    """Lazy local PP-OCR adapter used only for low-quality PDF pages."""

    def __init__(self):
        self._pipeline = None
        self._lock = threading.Lock()

    def _get_pipeline(self):
        with self._lock:
            if self._pipeline is None:
                try:
                    from paddleocr import PaddleOCR
                except ImportError as exc:
                    raise RuntimeError(
                        "OCR is required for this PDF page, but PaddleOCR is not installed. "
                        "Install requirements-ocr.txt or set OCR_ENABLED=false."
                    ) from exc
                self._pipeline = PaddleOCR(
                    lang=settings.OCR_LANGUAGE,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=settings.OCR_ENABLE_MKLDNN,
                )
        return self._pipeline

    def recognize(self, image) -> tuple[str, float | None]:
        import numpy as np

        texts: list[str] = []
        scores: list[float] = []
        pipeline = self._get_pipeline()
        with self._lock:
            for result in pipeline.predict(np.asarray(image)):
                data = _ocr_result_data(result)
                texts.extend(str(text).strip() for text in data.get("rec_texts", []) if str(text).strip())
                scores.extend(float(score) for score in data.get("rec_scores", []))
        confidence = sum(scores) / len(scores) if scores else None
        return "\n".join(texts), confidence


_default_ocr_engine = PpOcrEngine()


class PdfPlumberLoader:
    """Extract PDF body text and tables as separate documents."""

    def __init__(self, file_path: str, ocr_engine: PpOcrEngine | None = None):
        self.file_path = file_path
        self.ocr_engine = ocr_engine or _default_ocr_engine

    def load(self) -> list[Document]:
        import pdfplumber

        docs: list[Document] = []
        with pdfplumber.open(self.file_path) as pdf:
            for index, page in enumerate(pdf.pages):
                tables = page.find_tables()
                table_boxes = [table.bbox for table in tables]

                def outside_tables(obj: dict) -> bool:
                    x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                    y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                    return not any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in table_boxes)

                body_page = page.filter(outside_tables) if table_boxes else page
                text = body_page.extract_text() or ""
                extraction_method = "native"
                ocr_confidence = None
                if settings.OCR_ENABLED and _needs_ocr(text, has_images=bool(page.images)):
                    rendered = page.to_image(resolution=settings.OCR_RENDER_DPI).original
                    ocr_text, ocr_confidence = self.ocr_engine.recognize(rendered)
                    if ocr_text.strip():
                        text = ocr_text
                        extraction_method = "ocr"
                if text.strip():
                    docs.append(Document(
                        page_content=text.strip(),
                        metadata={
                            "page": index + 1,
                            "content_type": "text",
                            "extraction_method": extraction_method,
                            **({"ocr_confidence": round(ocr_confidence, 4)} if ocr_confidence is not None else {}),
                        },
                    ))

                for table_index, table in enumerate(tables, start=1):
                    markdown = _table_to_markdown(table.extract())
                    if markdown:
                        docs.append(Document(
                            page_content=f"[Table {table_index}]\n{markdown}",
                            metadata={
                                "page": index + 1,
                                "content_type": "table",
                                "table_index": table_index,
                            },
                        ))

        return docs


class RAGPipeline:
    """基于 LangChain 组件的 RAG 管道"""

    def __init__(self, connection_string: str | None = None):
        self.connection_string = connection_string or settings.DATABASE_URL
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", " ", ""],
        )
        self._vector_store = None

    def _get_embeddings(self):
        return create_embeddings()

    def _get_vector_store(self):
        if self._vector_store is not None:
            return self._vector_store

        embeddings = self._get_embeddings()

        if _is_postgres():
            from langchain_community.vectorstores import PGVector
            self._vector_store = PGVector(
                connection_string=_sync_db_url(self.connection_string),
                embedding_function=embeddings,
                collection_name=_collection_name(),
            )
        else:
            from langchain_chroma import Chroma
            persist_dir = Path("./data/chroma")
            persist_dir.mkdir(parents=True, exist_ok=True)
            logger.info("RAG 使用 Chroma 本地存储（开发环境 SQLite）")
            self._vector_store = Chroma(
                persist_directory=str(persist_dir),
                embedding_function=embeddings,
                collection_name=_collection_name(),
            )
        return self._vector_store

    def ingest_document(self, file_path: str, metadata: dict) -> int:
        file_hash = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
        metadata = {**metadata, "file_hash": file_hash}
        loader = self._get_loader(file_path)
        raw_docs = loader.load()
        for doc in raw_docs:
            doc.metadata.update(metadata)
        chunks = self._split_documents(raw_docs)
        for index, chunk in enumerate(chunks):
            chunk.metadata.update({"chunk_index": index, "chunk_count": len(chunks)})
        owner = str(metadata.get("user_id", ""))
        ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{owner}:{file_hash}:{index}")) for index in range(len(chunks))]
        self._vector_store_add(chunks, ids=ids)
        return len(ids)

    def _split_documents(self, documents: list[Document]) -> list[Document]:
        """Split body text across page boundaries while keeping tables independent."""
        text_docs = [doc for doc in documents if doc.metadata.get("content_type", "text") == "text"]
        other_docs = [doc for doc in documents if doc.metadata.get("content_type", "text") != "text"]
        chunks: list[Document] = []

        if text_docs:
            combined = "\n\n".join(doc.page_content for doc in text_docs)
            page_spans: list[tuple[int, int, int]] = []
            cursor = 0
            for doc in text_docs:
                end = cursor + len(doc.page_content)
                page_spans.append((cursor, end, int(doc.metadata.get("page", 1))))
                cursor = end + 2

            search_from = 0
            base_metadata = {
                key: value
                for key, value in text_docs[0].metadata.items()
                if key not in {"page", "page_start", "page_end"}
            }
            for content in self.text_splitter.split_text(combined):
                start = combined.find(content, max(0, search_from - settings.CHUNK_OVERLAP))
                if start < 0:
                    start = search_from
                end = start + len(content)
                covered_pages = [page for span_start, span_end, page in page_spans if span_start < end and span_end > start]
                covered_docs = [doc for doc in text_docs if int(doc.metadata.get("page", 1)) in covered_pages]
                methods = sorted({str(doc.metadata.get("extraction_method", "native")) for doc in covered_docs})
                confidences = [float(doc.metadata["ocr_confidence"]) for doc in covered_docs if "ocr_confidence" in doc.metadata]
                chunks.append(Document(
                    page_content=content,
                    metadata={
                        **base_metadata,
                        "content_type": "text",
                        "page_start": min(covered_pages),
                        "page_end": max(covered_pages),
                        "extraction_methods": ",".join(methods),
                        **({"ocr_confidence_min": min(confidences)} if confidences else {}),
                    },
                ))
                search_from = end

        for index in range(1, len(chunks)):
            previous = chunks[index - 1]
            current = chunks[index]
            if current.metadata["page_start"] > previous.metadata["page_end"]:
                overlap = previous.page_content[-settings.CHUNK_OVERLAP :].lstrip()
                if overlap:
                    current.page_content = f"{overlap}\n\n{current.page_content}"
                    current.metadata["page_start"] = previous.metadata["page_end"]

        if len(chunks) > 1 and len(chunks[-1].page_content) < 100:
            tail = chunks.pop()
            previous = chunks[-1]
            previous.page_content = f"{previous.page_content}\n\n{tail.page_content}"
            previous.metadata["page_end"] = tail.metadata.get("page_end", previous.metadata.get("page_end"))

        for doc in other_docs:
            for chunk in self.text_splitter.split_documents([doc]):
                page = int(chunk.metadata.get("page", 1))
                chunk.metadata.update({"page_start": page, "page_end": page})
                chunks.append(chunk)
        return chunks

    def _vector_store_add(self, chunks, ids: list[str] | None = None):
        store = self._get_vector_store()
        stored_ids = store.add_documents(chunks, ids=ids)
        if hasattr(store, "persist"):
            store.persist()
        return stored_ids

    @staticmethod
    def _distance_to_relevance(distance: float) -> float:
        """Chroma 返回的是距离（越小越相似），转换为 0~1 相关度"""
        return round(max(0.0, 1.0 - distance / 2.0), 3)

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[dict]:
        top_k = top_k or settings.RAG_TOP_K
        threshold = threshold if threshold is not None else settings.RAG_SIMILARITY_THRESHOLD
        store = self._get_vector_store()
        metadata_filter = {"user_id": user_id}

        if _is_postgres():
            docs_with_scores = store.similarity_search_with_relevance_scores(query, k=top_k, filter=metadata_filter)
            return [
                {"content": doc.page_content, "source": doc.metadata.get("source", ""), "score": round(score, 3)}
                for doc, score in docs_with_scores
                if score >= threshold
            ]

        # Chroma 的 relevance 分数偏低，用距离换算
        docs_with_scores = store.similarity_search_with_score(query, k=top_k, filter=metadata_filter)
        results = []
        for doc, distance in docs_with_scores:
            score = self._distance_to_relevance(distance)
            if score >= threshold:
                results.append({
                    "content": doc.page_content,
                    "source": doc.metadata.get("source", ""),
                    "score": score,
                })
        return results

    def resolve_search(self, query: str, user_id: str) -> tuple[str, bool, list[dict]]:
        results = self.search(query, user_id=user_id)
        if not has_relevant_hit(query, results):
            return KB_MISS_TEXT, False, []
        filtered = filter_results_by_terms(query, results)
        if not filtered:
            return KB_MISS_TEXT, False, []
        return format_knowledge_results(filtered), True, filtered

    def _get_loader(self, file_path: str):
        ext = file_path.rsplit(".", 1)[-1].lower()
        if ext == "pdf":
            return PdfPlumberLoader(file_path)
        if ext in ("docx", "doc"):
            from langchain_community.document_loaders import Docx2txtLoader
            return Docx2txtLoader(file_path)
        from langchain_community.document_loaders import TextLoader
        return TextLoader(file_path, encoding="utf-8")

    def as_langchain_tool(self):
        pipeline = self

        @tool
        def search_knowledge_base(query: str, user_id: str) -> str:
            """搜索知识库中的文档内容"""
            text, _, _ = pipeline.resolve_search(query, user_id=user_id)
            return text

        return search_knowledge_base


rag_pipeline = RAGPipeline()
