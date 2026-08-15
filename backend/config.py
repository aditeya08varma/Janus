"""Shared constants so ingest, query, eval, and Docker cannot silently drift."""

import os

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

RECURSION_LIMIT = 10
REDIS_TTL_MINUTES = 1440  # langgraph-checkpoint-redis: minutes, not seconds
RATE_LIMIT_PER_MINUTE = 20


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
