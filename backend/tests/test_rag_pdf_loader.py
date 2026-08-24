from langchain_core.documents import Document

from app.rag.pipeline import (
    PdfPlumberLoader,
    RAGPipeline,
    _needs_ocr,
    _ocr_result_data,
    _table_to_markdown,
)


def test_pdf_tables_are_rendered_as_markdown():
    table = [
        ["Drug", "Dose"],
        ["Aspirin", "100 mg"],
        ["Insulin", "10 units"],
    ]

    assert _table_to_markdown(table) == (
        "| Drug | Dose |\n"
        "| --- | --- |\n"
        "| Aspirin | 100 mg |\n"
        "| Insulin | 10 units |"
    )


def test_pdf_loader_is_used_for_pdf_files():
    loader = RAGPipeline()._get_loader("clinical-report.pdf")

    assert isinstance(loader, PdfPlumberLoader)


def test_native_text_quality_only_triggers_ocr_for_unusable_pages(monkeypatch):
    monkeypatch.setattr("app.rag.pipeline.settings.OCR_MIN_TEXT_LENGTH", 10)
    monkeypatch.setattr("app.rag.pipeline.settings.OCR_MIN_READABLE_RATIO", 0.7)

    assert _needs_ocr("") is True
    assert _needs_ocr("123") is False
    assert _needs_ocr("123", has_images=True) is True
    assert _needs_ocr("这是可以直接提取的正常中文页面。") is False
    assert _needs_ocr("����" * 10) is True


def test_ocr_result_adapter_reads_paddle_result_payload():
    class FakeResult:
        json = {"res": {"rec_texts": ["第一行", "第二行"], "rec_scores": [0.9, 0.8]}}

    assert _ocr_result_data(FakeResult()) == {
        "rec_texts": ["第一行", "第二行"],
        "rec_scores": [0.9, 0.8],
    }


def test_pdf_loader_falls_back_to_ocr_for_scanned_page(monkeypatch):
    class FakePage:
        images = [{}]

        def find_tables(self):
            return []

        def extract_text(self):
            return ""

        def to_image(self, resolution):
            assert resolution == 250
            return type("Rendered", (), {"original": object()})()

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class FakeOcr:
        def recognize(self, image):
            return "扫描页识别结果", 0.91

    monkeypatch.setattr("pdfplumber.open", lambda _: FakePdf())
    monkeypatch.setattr("app.rag.pipeline.settings.OCR_ENABLED", True)
    monkeypatch.setattr("app.rag.pipeline.settings.OCR_RENDER_DPI", 250)

    documents = PdfPlumberLoader("scan.pdf", ocr_engine=FakeOcr()).load()

    assert documents[0].page_content == "扫描页识别结果"
    assert documents[0].metadata == {
        "page": 1,
        "content_type": "text",
        "extraction_method": "ocr",
        "ocr_confidence": 0.91,
    }


def test_text_chunks_can_cross_page_boundaries_and_keep_page_range():
    pipeline = RAGPipeline()
    documents = [
        Document(page_content="甲" * 450, metadata={"page": 1, "content_type": "text", "source": "a.pdf"}),
        Document(page_content="乙" * 450, metadata={"page": 2, "content_type": "text", "source": "a.pdf"}),
    ]

    chunks = pipeline._split_documents(documents)

    assert chunks[1].metadata["page_start"] == 1
    assert chunks[1].metadata["page_end"] == 2
    assert "甲" in chunks[1].page_content
    assert "乙" in chunks[1].page_content


def test_short_trailing_text_is_merged_instead_of_becoming_an_orphan_chunk():
    pipeline = RAGPipeline()
    documents = [
        Document(page_content="正文。" * 300, metadata={"page": 1, "content_type": "text"}),
        Document(page_content="短尾页", metadata={"page": 2, "content_type": "text"}),
    ]

    chunks = pipeline._split_documents(documents)

    assert len(chunks[-1].page_content) >= 100
    assert "短尾页" in chunks[-1].page_content
    assert chunks[-1].metadata["page_end"] == 2


def test_tables_stay_in_independent_chunks():
    pipeline = RAGPipeline()
    documents = [
        Document(page_content="正文内容", metadata={"page": 1, "content_type": "text"}),
        Document(
            page_content="[Table 1]\n| 药品 | 剂量 |",
            metadata={"page": 1, "content_type": "table", "table_index": 1},
        ),
    ]

    chunks = pipeline._split_documents(documents)

    assert {chunk.metadata["content_type"] for chunk in chunks} == {"text", "table"}
    table_chunk = next(chunk for chunk in chunks if chunk.metadata["content_type"] == "table")
    assert table_chunk.metadata["page_start"] == 1
    assert table_chunk.metadata["page_end"] == 1


def test_search_applies_user_metadata_filter(monkeypatch):
    class FakeStore:
        def similarity_search_with_score(self, query, k, filter):
            assert filter == {"user_id": "user-1"}
            return []

    pipeline = RAGPipeline(connection_string="sqlite:///test.db")
    monkeypatch.setattr(pipeline, "_get_vector_store", lambda: FakeStore())

    assert pipeline.search("急救", user_id="user-1") == []


def test_ingest_uses_stable_ids_for_duplicate_file(monkeypatch, tmp_path):
    file_path = tmp_path / "same.txt"
    file_path.write_text("同一份文档内容", encoding="utf-8")
    pipeline = RAGPipeline()
    captured_ids: list[list[str]] = []

    class FakeLoader:
        def load(self):
            return [Document(page_content="同一份文档内容", metadata={"content_type": "text"})]

    monkeypatch.setattr(pipeline, "_get_loader", lambda _: FakeLoader())
    monkeypatch.setattr(
        pipeline,
        "_vector_store_add",
        lambda chunks, ids=None: captured_ids.append(ids or []),
    )

    metadata = {"user_id": "user-1", "document_id": "document-1", "source": "same.txt"}
    pipeline.ingest_document(str(file_path), metadata)
    pipeline.ingest_document(str(file_path), {**metadata, "document_id": "document-2"})

    assert captured_ids[0] == captured_ids[1]
