# Implemented bugfixes

This document describes the production gaps that were present in Janus 2.0, what each one actually broke, and how the current code fixes it.

The original list mixed **correctness bugs** (wrong answers), **reliability bugs** (crashes / silent data loss), **security gaps**, and **frontend bugs**. They are grouped the same way here.

---

## How to read this

For every item:

1. **Problem** — what the old code did, with the failure mode.
2. **Fix** — what changed.
3. **Why this works** — the mechanism, including trade-offs that were kept on purpose.

Shared constants live in `backend/config.py` so ingest, query, eval, and Docker cannot silently disagree about the embedding model or index shape.

---

## Retrieval / RAG

### 1. CheatSheet was permanently unreachable

**Problem.** `concepts.txt` was ingested with `year=0`. `search_knowledge_base` filtered Pinecone with `year ∈ {target_year, target_year-1}` (typically `{2026, 2025}`). Year `0` never matched, so the synonym sheet sat in the index, cost storage, and was never retrieved. That meant queries using FIA jargon aliases (“Overtake Mode”, “X-Mode”) had to rely on raw semantic similarity instead of the glossary that was built for them.

**Fix.** Retrieval now uses a single `$or` filter: matching years **or** `source = CheatSheet`. CheatSheet hits are tagged `[[SYNONYM CHEATSHEET]]` rather than `[[OFFICIAL FINALIZED]]`, so the LLM does not treat a glossary row as a regulation.

**Why this works.** Pinecone applies metadata filters before ANN search. Including CheatSheet in the candidate set means it can actually be nearest-neighbor ranked. One query (not a second unfiltered search) avoids doubling latency. The distinct status tag prevents a synonym list from being cited as Issue 15.

---

### 2. Same-priority cross-year ties were ordered by wording luck

**Problem.** Results were sorted by `priority` only. Python’s sort is stable, so two `priority=1` chunks (2025 vs 2026) kept Pinecone’s cosine order. If the user’s phrasing was closer to an older paragraph, a superseded number could appear first in context and get copied into the answer.

**Fix.** Sort key is `(CheatSheet first, priority, −year)`. Recency is a **tie-break among already-retrieved hits**, not a reason to drop older years.

**Why this works.** Hierarchical fallback still depends on older years being *present* in the result set (see #3). Recency only decides order once they are present. A global “always prefer newer” filter would hide silently inherited rules. Conflict tagging (#conflict below) then tells the model when two finalized years disagree.

---

### 3. One-year lookback missed facts restated 2+ years back

**Problem.** The filter was hard-coded to `[target_year, target_year-1]`. A 2026 question about a clause last written in 2024 returned nothing useful, with no signal that the window was too narrow.

**Fix.** Start with the resolved year set. If the best cosine score is below `CASCADE_SCORE_THRESHOLD` (0.40) and results are empty/weak, widen lookback one year at a time, capped at `CASCADE_MAX_DEPTH` (3) and not before 2022.

**Why this works.** Blindly searching `{2026,2025,2024,2023}` would reintroduce superseded numbers into every query. Gating on score keeps the cheap, tight window for strong hits and only spends extra recall when the tight window failed. The cap is empirical: this corpus only exists from 2022.

---

### 4. Table-unaware chunking split headers from data

**Problem.** `RecursiveCharacterTextSplitter(1000, 100)` does not understand markdown tables. A long FIA table (the actual kW / kg / mm figures) could put `| Metric | Value |` in chunk A and the rows in chunk B. The retrieved chunk then had numbers without column meaning, which is a direct hallucination path.

**Fix.** `backend/chunking.py` detects markdown table blocks. Tables are split only if they exceed `TABLE_CHUNK_SIZE` (2000), and **every piece repeats the header row**. Prose still uses 1000/100.

**Why this works.** Embedding a headerless row loses the schema; repeating the header makes each table chunk self-contained. Global larger `chunk_size` would dilute prose embeddings, so the larger limit is table-specific.

---

### 5. Embedding model could drift between ingest and query

**Problem.** `ingest.py` and `graph.py` (and `run_ragas.py`) each hard-coded `"all-MiniLM-L6-v2"`. A one-sided edit would embed queries into a different space than the index. Cosine search would still return “confident” neighbors — they would just be wrong. That failure is silent.

**Fix.** `EMBEDDING_MODEL` and `EMBEDDING_DIM` live in `config.py`. Ingest, `graph.py`, Ragas, and the Docker pre-bake all import them. The Dockerfile copies `config.py` before baking `SentenceTransformer(EMBEDDING_MODEL)` and the cross-encoder.

**Why this works.** A mismatch is now a single-constant change, not three string literals. Dimension is tied to the same file so a model swap cannot recreate a 384-vs-other index.

---

### Cross-year conflict flagging

**Problem.** Two `priority=1` chunks from 2025 and 2026 were both labeled `OFFICIAL FINALIZED`. The model had to notice the year tags itself.

**Fix.** If the result set contains finalized docs from 2+ years, the context string starts with `[[CROSS-YEAR CONFLICT]]` and older finalized chunks are tagged `MAY BE SUPERSEDED BY NEWER YEAR`.

**Why this works.** The instruction is in the retrieved context, which the model attends to more reliably than a buried system-prompt rule. Comparison queries still have both years; the banner tells the model to prefer the newest unless the user asked to compare.

---

### Cross-encoder reranker (context precision)

**Problem.** Ragas `context_precision` was ~0.56 because `k=8` over-retrieves. Irrelevant high-ranked chunks hurt the metric and steal context window.

**Fix.** Retrieve up to 24 candidates, then rerank with `cross-encoder/ms-marco-MiniLM-L-6-v2` down to 8. Disable with `ENABLE_RERANKER=0`. If the model fails to load, retrieval continues with bi-encoder order.

**Why this works.** A bi-encoder encodes query and doc separately (fast, approximate). A cross-encoder scores the pair jointly (slower, more accurate). Using it only on a shortlist keeps latency bounded. Tests disable it so CI does not download the model.

---

### Deterministic year extraction

**Problem.** `target_year` was only an LLM tool argument. The model could ignore “2024 vs 2026” and search 2026. `search_web` always appended `"F1 2026"`.

**Fix.** Regex extraction (`year_extract.py`) runs on the user text before the LLM call. Mentioned years are appended as a `[YEAR HINT]`. The retriever uses mentioned years when present, so a 2024-vs-2026 comparison searches those years rather than `[2026, 2025]`. Web search no longer hard-codes 2026.

**Why this works.** Regex cannot misread a four-digit year in this domain. The hint does not replace the LLM for ambiguous wording (“current regs”); it overrides when years are explicit. Multiple years are kept as a set — extracting a *single* year would have broken comparisons.

---

## Backend infra / reliability

### 6. Redis + graph compiled on every request

**Problem.** `/chat` opened `AsyncRedisSaver.from_conn_string` and called `graph_builder.compile()` per request. Connection setup and compile cost were paid on every turn.

**Fix.** FastAPI `lifespan` opens Redis once, compiles once, stores the graph on `app.state`. Set `JANUS_SKIP_REDIS=1` (tests) to use `MemorySaver`.

**Why this works.** The checkpointer is process-wide and safe to share; isolation is by `thread_id`, not by connection. Workers stay stateless; Redis still holds session state.

---

### 7. Connection-open errors were uncaught (and untested)

**Problem.** `try/except` wrapped only `astream()`. A Redis failure during `from_conn_string` happened before the first `yield`, so FastAPI returned an unhandled 500 instead of a streamed `[CRITICAL ERROR]`. `test_ai_brain_crash` sabotaged `astream` while mocking Redis as healthy — it never tested connection-open.

**Fix.** Connection errors happen in `lifespan` and fail startup (the process should not serve traffic without a checkpointer). Per-request `astream` errors are still streamed as `[CRITICAL ERROR]`. Tests now assert compile happens once per process and that graph failures still stream the error string.

**Why this works.** A dead Redis at boot is an ops failure, not a per-user recoverable stream. Hiding it inside a 200 body made the service look up while every request was broken.

---

### 8. No explicit recursion limit

**Problem.** The ReAct loop continues while `tool_calls` exist. LangGraph’s default cap is 25 hops. A pathological loop burned tokens until that default tripped.

**Fix.** Runtime config sets `recursion_limit=10` (`config.RECURSION_LIMIT`).

**Why this works.** This domain almost never needs more than two tool calls. A tighter cap fails faster and cheaper. `temperature=0` still helps convergence; the limit is the hard stop.

---

### 9. Redis checkpoints never expired

**Problem.** No TTL. Closing a tab orphaned `thread_id` keys until LRU eviction. `summary.md` originally used `86400` as if the unit were seconds; in this library `default_ttl` is **minutes** (`1440` = 24 hours).

**Fix.** `ttl={"default_ttl": 1440, "refresh_on_read": True}`. Active threads refresh on each turn (sliding expiration). Abandoned threads age out. If the installed saver does not accept `ttl=`, startup logs a warning and still connects.

**Why this works.** Sliding TTL keeps a live conversation alive without retaining forever-idle keys. Pair with `localStorage` (#18) so the browser still has the same `session_id` after a restart, within the TTL window.

---

### 10. Bare `except:` in `search_web`

**Problem.** `except:` swallowed `KeyboardInterrupt` / `SystemExit` and hid the real error.

**Fix.** `except Exception as e` with `logger.exception`. The tool still returns a safe fallback string so the agent can continue on specs.

---

### 11. Failed PDF downloads were written as if valid

**Problem.** `requests.get` wrote `r.content` with no `raise_for_status()` and no content-type check. A 404 HTML page became `2026_regs_tech_iss15.pdf` and was parsed as regulations.

**Fix.** `raise_for_status()`, require `%PDF` magic bytes (or a PDF content-type), log failures, skip the file instead of writing garbage. The pipeline is also behind `if __name__ == "__main__"` so importing `ingest` cannot run downloads.

---

### 12. Every ingest wiped the entire Pinecone index

**Problem.** `delete_index` + `create_index` on every run. A failed run after delete meant an empty production index. Importing the module was enough to wipe it.

**Fix.** Default path is incremental: create the index only if missing; for each source file, metadata-filtered delete then upsert with deterministic IDs. Full wipe is opt-in: `python ingest.py --fresh`.

**Why this works.** Re-running ingest after a LlamaParse fix to one PDF no longer rebuilds 2022–2026. Deterministic IDs keep upserts idempotent. `--fresh` remains for a true rebuild.

---

## Security

### 13. `/chat` had no auth or rate limit

**Problem.** CORS only affects browsers. `curl` could hit `/chat` unbounded. LLM + Pinecone + Redis cost is attacker-controlled.

**Fix.** Optional `JANUS_API_KEY` (header `X-API-Key`). If unset, local/dev stays open. If set, missing/wrong keys get 401. In-memory rate limit: 20 requests / IP / minute → 429. Frontend sends `VITE_JANUS_API_KEY` when configured.

**Why this works.** This is application-level auth, not user accounts. It stops anonymous abuse of a public GPU/LLM bill. CORS is unchanged and still not an auth boundary.

---

### 14. `session_id` is still a correlation key

**Problem.** Anyone who knows a `thread_id` can read/append that conversation. An API key shared by the whole frontend does **not** bind a key to a thread.

**Fix.** API key + rate limit raise the floor. History and chat both require the key when configured. True per-user authorization would need accounts and server-issued session tokens — out of scope for a portfolio chatbot with no PII.

**Honest remainder.** `thread_id` is still not an authorization boundary. Do not store secrets in chat.

---

### 15. `Math.random()` session IDs

**Problem.** `Date.now() + Math.random().toString(36)` is not a CSPRNG. Guessing was low-probability but the ID was doing double duty as a capability.

**Fix.** `crypto.randomUUID()`.

**Why this works.** UUIDv4 is the right generator for an unguessable correlation ID. It still does not replace auth (#14).

---

## Frontend

### 17. Dead `!m.isUser` condition hid a real telemetry bug

**Problem.** Message objects have `role: 'user' | 'bot'`, never `isUser`. `!m.isUser` was always true. Telemetry only rendered when `!content.trim()`, so **the first answer token hid the logs**.

**Fix.** `{m.role === 'bot' && logs.length > 0 && <TelemetryConsole />}` sits above the typewriter, not in an exclusive branch.

**Why this works.** User bubbles never have `__LOG__` lines. Bot bubbles can have logs and content at the same time.

---

### 18. Reload showed a blank UI while Redis still had the thread

**Problem.** `messages` started as `[]`. `sessionStorage` kept `session_id` on same-tab reload (so Redis was valid) but the UI did not fetch it. Closing the tab dropped `sessionStorage` and orphaned the Redis key.

**Fix.**

- `localStorage` for `janus_session_id` and cached messages (instant paint).
- `GET /history/{session_id}` hydrates from the LangGraph checkpoint (source of truth).
- New turns no longer re-inject `SYSTEM_PROMPT` if the thread already has messages, so checkpoints do not accumulate duplicate system prompts.

**Why this works.** localStorage covers tab close; Redis TTL covers abandoned keys; the history endpoint maps Human/AI messages back into `{role, text}` including `__LOG__` lines reconstructed from tool calls.

---

## What to set in production

| Variable | Purpose |
|---|---|
| `REDIS_URL` | Checkpointer. Omit/`JANUS_SKIP_REDIS=1` only for tests. |
| `JANUS_API_KEY` | Require `X-API-Key` on `/chat` and `/history`. |
| `VITE_JANUS_API_KEY` | Frontend copy of the same key. |
| `ENABLE_RERANKER` | Default on. Set `0` to skip the cross-encoder. |
| `FRONTEND_URL` | Extra CORS origin. |

Ingest:

```bash
python ingest.py          # incremental upsert
python ingest.py --fresh  # destructive rebuild (explicit)
```

---

## Tests added or updated

- `tests/test_retrieval.py` — year extraction, CheatSheet filter, recency sort, conflict tags, cascade threshold, table header preservation.
- `tests/test_api_smoke.py` / `test_api_failures.py` — lifespan compile-once, streamed graph errors, `/history`, API key 401.
- Tests set `JANUS_SKIP_REDIS=1` and `ENABLE_RERANKER=0` so they do not need Redis or the cross-encoder.
