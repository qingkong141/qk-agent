from app.rag.pipeline import PdfPlumberLoader, RAGPipeline, _table_to_markdown


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
