"""Shared constants so ingest, query, eval, and Docker cannot silently drift."""

import os
import threading

# Shared across graph.py (vectorstore) and retrieval.py (reranker): the two
# local ML models must not be constructed concurrently on separate locks.
# Concurrent first-touch of PyTorch/tokenizers' native init from two threads
# at once — even for two *different* model objects — has been observed to
# segfault the process. One shared lock serializes all first-time local model
# construction, regardless of which model or which code path reaches it first.
ML_INIT_LOCK = threading.Lock()


def load_offline_first(loader):
    """Construct a HuggingFace model with HF_HUB_OFFLINE=1 first, falling
    back to a normal online load if it isn't cached locally yet.

    By default, huggingface_hub re-validates cache freshness over the
    network on every construction — even when the model is already fully
    cached — via a burst of HEAD requests, some done through its own internal
    thread pool. That's harmless standalone, but observed to cause severe
    GIL contention (a routine ~5s load stretching past a minute) when it
    happens inside an already-multithreaded server under ML_INIT_LOCK.
    Skipping it is safe here because the Dockerfile pre-bakes both models at
    build time specifically so they're always present at runtime; the
    online fallback only matters for a fresh local dev environment that
    hasn't loaded them once yet.
    """
    prev = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        return loader()
    except Exception:
        if prev is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prev
        return loader()

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
INDEX_NAME = "f1-regulations-all"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

MIN_REG_YEAR = 2022
DEFAULT_YEAR = 2026

RETRIEVAL_K = 8
RERANK_CANDIDATES = 24
CASCADE_SCORE_THRESHOLD = 0.40
CASCADE_MAX_DEPTH = 3

CHEATSHEET_SOURCE = "CheatSheet"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
TABLE_CHUNK_SIZE = 2000

RECURSION_LIMIT = 16  # 8 agent/tool round-trips. The tool-budget prompt rule (api.py
# SYSTEM_PROMPT) usually converges in 3-4 rounds, but LLM output isn't perfectly
# deterministic even at temperature=0 — an occasional extra round shouldn't be a
# hard failure. 16 still catches a genuinely runaway loop; it just stops treating
# "one round slower than usual" as one.
REDIS_TTL_MINUTES = 1440  # langgraph-checkpoint-redis: minutes, not seconds
RATE_LIMIT_PER_MINUTE = 20


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
