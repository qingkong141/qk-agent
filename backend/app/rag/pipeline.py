import re
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


class PdfPlumberLoader:
    """Extract PDF text plus tables, preserving tables as Markdown for RAG chunks."""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        import pdfplumber

        docs: list[Document] = []
        with pdfplumber.open(self.file_path) as pdf:
            for index, page in enumerate(pdf.pages):
                parts: list[str] = []
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text.strip())

                tables = page.extract_tables() or []
                for table_index, table in enumerate(tables, start=1):
                    markdown = _table_to_markdown(table)
                    if markdown:
                        parts.append(f"[Table {table_index}]\n{markdown}")

                if parts:
                    docs.append(Document(
                        page_content="\n\n".join(parts),
                        metadata={"page": index},
                    ))

        return docs


class RAGPipeline:
    """基于 LangChain 组件的 RAG 管道"""

    def __init__(self, connection_string: str | None = None):
        self.connection_string = connection_string or settings.DATABASE_URL
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", ".", " ", ""],
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
        loader = self._get_loader(file_path)
        raw_docs = loader.load()
        for doc in raw_docs:
            doc.metadata.update(metadata)
        chunks = self.text_splitter.split_documents(raw_docs)
        ids = self._vector_store_add(chunks)
        return len(ids)

    def _vector_store_add(self, chunks):
        store = self._get_vector_store()
        ids = store.add_documents(chunks)
        if hasattr(store, "persist"):
            store.persist()
        return ids

    @staticmethod
    def _distance_to_relevance(distance: float) -> float:
        """Chroma 返回的是距离（越小越相似），转换为 0~1 相关度"""
        return round(max(0.0, 1.0 - distance / 2.0), 3)

    def search(self, query: str, top_k: int | None = None, threshold: float | None = None) -> list[dict]:
        top_k = top_k or settings.RAG_TOP_K
        threshold = threshold if threshold is not None else settings.RAG_SIMILARITY_THRESHOLD
        store = self._get_vector_store()

        if _is_postgres():
            docs_with_scores = store.similarity_search_with_relevance_scores(query, k=top_k)
            return [
                {"content": doc.page_content, "source": doc.metadata.get("source", ""), "score": round(score, 3)}
                for doc, score in docs_with_scores
                if score >= threshold
            ]

        # Chroma 的 relevance 分数偏低，用距离换算
        docs_with_scores = store.similarity_search_with_score(query, k=top_k)
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

    def resolve_search(self, query: str) -> tuple[str, bool, list[dict]]:
        results = self.search(query)
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
        def search_knowledge_base(query: str) -> str:
            """搜索知识库中的文档内容"""
            text, _, _ = pipeline.resolve_search(query)
            return text

        return search_knowledge_base


rag_pipeline = RAGPipeline()
