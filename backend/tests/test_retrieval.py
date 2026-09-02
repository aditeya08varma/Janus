from types import SimpleNamespace

from year_extract import extract_years, resolve_search_years, year_hint
from retrieval import (
    conflict_banner,
    format_context,
    needs_cascade,
    pinecone_year_filter,
    sort_results,
    status_for,
)
from chunking import split_documents_table_aware, split_table_preserving_header
from langchain_core.documents import Document


def test_extract_years_unique_order():
    assert extract_years("Compare DRS in 2025 with Active Aero in 2026") == [2025, 2026]
    assert extract_years("What is min weight?") == []
    assert extract_years("1998 championship") == []


def test_resolve_search_years_comparison_keeps_both():
    years = resolve_search_years("2024 vs 2026 fuel flow", target_year=2026)
    assert years == [2026, 2024]


def test_resolve_search_years_single_adds_lookback():
    assert resolve_search_years("MGU-K power in 2026", 2026) == [2026, 2025]


def test_year_hint_appended():
    hint = year_hint("minimum weight 2025")
    assert "2025" in hint
    assert year_hint("hello") == ""


def test_cheatsheet_in_year_filter():
    filt = pinecone_year_filter([2026, 2025])
    assert filt["$or"][0] == {"source": {"$eq": "CheatSheet"}}
    assert {"year": {"$in": [2026, 2025]}} in filt["$or"]


def test_sort_recency_tiebreak():
    older = SimpleNamespace(metadata={"priority": 1, "year": 2025, "source": "a.pdf"})
    newer = SimpleNamespace(metadata={"priority": 1, "year": 2026, "source": "b.pdf"})
    cheat = SimpleNamespace(metadata={"priority": 1, "year": 0, "source": "CheatSheet"})
    ordered = sort_results([older, newer, cheat])
    assert ordered[0] is cheat
    assert ordered[1] is newer
    assert ordered[2] is older


def test_conflict_banner_and_superseded_tag():
    docs = [
        SimpleNamespace(
            metadata={"priority": 1, "year": 2026, "source": "2026.pdf"},
            page_content="350 kW",
        ),
        SimpleNamespace(
            metadata={"priority": 1, "year": 2025, "source": "2025.pdf"},
            page_content="120 kW",
        ),
    ]
    banner = conflict_banner(docs)
    assert "CROSS-YEAR CONFLICT" in banner
    assert "SUPERSEDED" in status_for(docs[1], True, 2026)
    text = format_context(docs)
    assert "CROSS-YEAR CONFLICT" in text


def test_low_confidence_tag_from_reranker_score():
    doc = SimpleNamespace(metadata={"priority": 1, "year": 2026, "source": "2026.pdf"})
    assert "LOW CONFIDENCE" not in status_for(doc, True, 2026, score=None)
    assert "LOW CONFIDENCE" not in status_for(doc, True, 2026, score=2.5)
    assert "LOW CONFIDENCE" in status_for(doc, True, 2026, score=-1.2)


def test_format_context_threads_scores_from_rerank_pairs():
    strong = SimpleNamespace(
        metadata={"priority": 1, "year": 2026, "source": "2026.pdf"},
        page_content="350 kW",
    )
    weak = SimpleNamespace(
        metadata={"priority": 1, "year": 2026, "source": "2026.pdf"},
        page_content="unrelated aside",
    )
    text = format_context([(strong, 4.1), (weak, -3.0)])
    entries = text.split("---")
    assert "LOW CONFIDENCE" not in entries[0]
    assert "LOW CONFIDENCE" in entries[1]
    # Plain Document list (no reranker scores available) must still work.
    assert "LOW CONFIDENCE" not in format_context([strong, weak])


def test_needs_cascade():
    assert needs_cascade([]) is True
    assert needs_cascade([0.21, 0.18]) is True
    assert needs_cascade([0.72, 0.41]) is False


def test_table_header_repeated_on_split():
    header = "| Metric | Value |\n| --- | --- |"
    rows = "\n".join(f"| row{i} | {i} |" for i in range(80))
    table = f"{header}\n{rows}"
    chunks = split_table_preserving_header(table, max_size=200)
    assert len(chunks) > 1
    assert all(chunk.startswith("| Metric | Value |") for chunk in chunks)


def test_table_aware_chunking_keeps_header_with_rows():
    prose = "Article C5.1 defines power.\n\n"
    table = "| Unit | Limit |\n| --- | --- |\n| MGU-K | 350 kW |\n"
    doc = Document(page_content=prose + table, metadata={"source": "x.pdf", "year": 2026})
    chunks = split_documents_table_aware([doc])
    table_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
    assert table_chunks
    assert "MGU-K" in table_chunks[0].page_content
    assert "Unit" in table_chunks[0].page_content
