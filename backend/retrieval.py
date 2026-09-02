"""Pure retrieval helpers — filter, sort, tag, cascade, optional rerank."""

import logging
from typing import Iterable, List, Optional, Sequence, Tuple

from config import (
    CASCADE_MAX_DEPTH,
    CASCADE_SCORE_THRESHOLD,
    CHEATSHEET_SOURCE,
    DEFAULT_YEAR,
    LOW_CONFIDENCE_THRESHOLD,
    ML_INIT_LOCK,
    MIN_REG_YEAR,
    RERANK_CANDIDATES,
    RERANKER_MODEL,
    RETRIEVAL_K,
    env_flag,
    load_offline_first,
)

logger = logging.getLogger(__name__)

_reranker = None
_reranker_failed = False


def pinecone_year_filter(years: Sequence[int]) -> dict:
    """Year filter that always includes the synonym CheatSheet (year=0)."""
    clauses = [{"source": {"$eq": CHEATSHEET_SOURCE}}]
    if years:
        clauses.append({"year": {"$in": list(years)}})
    return {"$or": clauses}


def _year_int(doc) -> int:
    try:
        return int(doc.metadata.get("year") or 0)
    except (TypeError, ValueError):
        return 0


def sort_key(doc) -> Tuple[int, int, int]:
    """
    CheatSheet first (synonyms help the LLM), then priority (1 before 2),
    then recency. Recency is a tie-break among same-priority hits so a
    superseded figure cannot outrank the current one on wording luck alone.
    Hierarchical fallback still works because older years are only present
    when cascade/lookback brought them in.
    """
    if doc.metadata.get("source") == CHEATSHEET_SOURCE:
        return (0, 0, 0)
    priority = int(doc.metadata.get("priority", 2) or 2)
    return (1, priority, -_year_int(doc))


def sort_results(docs: List) -> List:
    return sorted(docs, key=sort_key)


def status_for(doc, has_finalized: bool, newest_final_year: Optional[int], score: Optional[float] = None) -> str:
    if doc.metadata.get("source") == CHEATSHEET_SOURCE:
        tag = "[[SYNONYM CHEATSHEET]]"
    else:
        priority = doc.metadata.get("priority", 2)
        if priority == 1:
            year = _year_int(doc)
            if (
                newest_final_year is not None
                and year
                and year < newest_final_year
            ):
                tag = "[[OFFICIAL FINALIZED — MAY BE SUPERSEDED BY NEWER YEAR]]"
            else:
                tag = "[[OFFICIAL FINALIZED REGULATION]]"
        elif has_finalized:
            tag = "[[OBSOLETE DRAFT]]"
        else:
            tag = "[[PROVISIONAL DRAFT]]"
    if score is not None and score < LOW_CONFIDENCE_THRESHOLD:
        # The cross-encoder's own judgment that this chunk isn't a strong
        # match for the query, computed on every search since the recursion-
        # loop investigation but discarded until now — surfaced here so the
        # agent can weigh a weak match differently instead of treating every
        # retrieved chunk as equally trustworthy.
        tag = f"{tag} [[LOW CONFIDENCE MATCH — relevance score {score:.2f}]]"
    return tag


def conflict_banner(docs: Sequence) -> str:
    final_years = sorted(
        {
            _year_int(d)
            for d in docs
            if d.metadata.get("priority") == 1
            and d.metadata.get("source") != CHEATSHEET_SOURCE
            and _year_int(d) > 0
        }
    )
    if len(final_years) < 2:
        return ""
    return (
        f"[[CROSS-YEAR CONFLICT]] Official finalized text from years "
        f"{final_years} is in this context. Prefer the newest year unless "
        f"the user is asking for a comparison. Older-year figures may be superseded.\n---\n"
    )


def format_context(ranked: Sequence) -> str:
    """ranked: a sequence of Documents, or (Document, score) pairs from
    rerank_docs — score is the cross-encoder's relevance judgment, used to
    tag weak matches so the agent doesn't treat every chunk as equally
    trustworthy. Plain Documents (no score available) still work."""
    if not ranked:
        return "No relevant regulations found."
    pairs = [item if isinstance(item, tuple) else (item, None) for item in ranked]
    docs = [d for d, _ in pairs]
    has_finalized = any(
        d.metadata.get("priority") == 1
        and d.metadata.get("source") != CHEATSHEET_SOURCE
        for d in docs
    )
    final_years = [
        _year_int(d)
        for d in docs
        if d.metadata.get("priority") == 1
        and d.metadata.get("source") != CHEATSHEET_SOURCE
        and _year_int(d) > 0
    ]
    newest_final = max(final_years) if final_years else None
    parts = [conflict_banner(docs)]
    for doc, score in pairs:
        status = status_for(doc, has_finalized, newest_final, score)
        parts.append(
            f"SOURCE: {doc.metadata.get('source')} | YEAR: {doc.metadata.get('year')} | STATUS: {status}\n"
            f"CONTENT: {doc.page_content}\n"
        )
    return "\n---\n".join(p for p in parts if p)


def needs_cascade(scores: Iterable[float], threshold: float = CASCADE_SCORE_THRESHOLD) -> bool:
    scores = list(scores)
    if not scores:
        return True
    return max(scores) < threshold


def cascade_years(base_years: Sequence[int], depth: int) -> List[int]:
    """Widen lookback by `depth` additional years below the newest base year."""
    newest = max(base_years) if base_years else DEFAULT_YEAR
    years = set(base_years)
    for step in range(1, depth + 1):
        candidate = newest - step
        if candidate >= MIN_REG_YEAR:
            years.add(candidate)
    return sorted(years, reverse=True)


def max_cascade_depth(base_years: Sequence[int]) -> int:
    newest = max(base_years) if base_years else DEFAULT_YEAR
    return min(CASCADE_MAX_DEPTH, max(0, newest - MIN_REG_YEAR))


def get_reranker():
    """Thread-safe lazy singleton — see get_vectorstore() in graph.py for why this matters
    now that tool calls run concurrently."""
    global _reranker, _reranker_failed
    if _reranker_failed or not env_flag("ENABLE_RERANKER", default=True):
        return None
    if _reranker is None:
        with ML_INIT_LOCK:
            if _reranker is None and not _reranker_failed:
                try:
                    import torch
                    from sentence_transformers import CrossEncoder

                    torch.set_num_threads(1)  # see graph.get_vectorstore() for why
                    # device="cpu": see graph.get_vectorstore() — MPS autodetection
                    # isn't safe under concurrent tool calls, and prod has no GPU.
                    _reranker = load_offline_first(lambda: CrossEncoder(RERANKER_MODEL, device="cpu"))
                except Exception:
                    logger.exception("Cross-encoder reranker unavailable; using bi-encoder order")
                    _reranker_failed = True
    return _reranker


def rerank_docs(query: str, docs: List, top_k: int = RETRIEVAL_K) -> List[Tuple]:
    """Returns (doc, score) pairs, sorted by relevance descending, truncated
    to top_k. score is None when the reranker isn't available (bi-encoder
    order kept as-is) — format_context treats a None score as "no
    confidence signal" rather than tagging it low-confidence."""
    if not docs:
        return []
    model = get_reranker()
    if model is None:
        return [(d, None) for d in docs[:top_k]]
    pairs = [(query, d.page_content) for d in docs]
    scores = model.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda item: float(item[1]), reverse=True)
    top = ranked[:top_k]
    logger.info("rerank query=%r top_%d_scores=%s", query, top_k, [round(float(s), 3) for _, s in top])
    return [(doc, float(score)) for doc, score in top]


def retrieve_with_cascade(vectorstore, query: str, search_years: Sequence[int], k: int = RERANK_CANDIDATES):
    """
    Query Pinecone, widening the year window only when the best hit is weak.
    Returns (docs, years_used).
    """
    years = list(search_years)
    scored: List[Tuple] = []
    depth_cap = max_cascade_depth(years)
    depth = 0

    while True:
        filt = pinecone_year_filter(years)
        try:
            scored = vectorstore.similarity_search_with_score(query, k=k, filter=filt)
        except Exception:
            logger.exception("similarity_search_with_score failed; falling back")
            docs = vectorstore.similarity_search(query, k=k, filter=filt)
            scored = [(d, 1.0) for d in docs]

        scores = [s for _, s in scored]
        logger.info(
            "search_knowledge_base query=%r years=%s best_score=%.4f scores=%s",
            query, years, max(scores) if scores else 0.0, [round(s, 3) for s in scores],
        )
        if not needs_cascade(scores) or depth >= depth_cap:
            break
        depth += 1
        widened = cascade_years(search_years, depth)
        if set(widened) == set(years):
            break
        logger.info("Cascading year fallback to %s (best score too weak)", widened)
        years = widened

    docs = [d for d, _ in scored]
    return docs, years
