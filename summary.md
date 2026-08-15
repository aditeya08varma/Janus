# Janus 2.0 — Resume & Interview Prep Summary

A complete record of the chat: codebase audit, resume bullet review, defense scripts, architectural narratives, measurement methodology, and tech-stack rationale.

---

## 1. Project Overview (verified from codebase)

**Janus 2.0** — F1 Technical Regulation Intelligence chatbot. RAG agent that answers questions about FIA Formula 1 regulations across the 2022-2026 era transition (Ground Effect → Nimble Car).

### Stack confirmed in code

- **LLM**: DeepSeek-V3 (`langchain_deepseek.ChatDeepSeek`, `temperature=0`, `max_retries=2`), env key `HUGIN`
- **Vector DB**: Pinecone serverless (AWS us-east-1, dim=384, cosine), index `f1-regulations-all`, env key `MUNIN`
- **Embeddings**: HuggingFace `all-MiniLM-L6-v2` (384-dim)
- **Session memory**: `AsyncRedisSaver` (LangGraph Redis checkpointer), env key `REDIS_URL`
- **Orchestration**: LangGraph `StateGraph` — single agent node + tools node + named conditional router
- **API**: FastAPI with `StreamingResponse(media_type="text/plain")`, CORS configured
- **Frontend**: React 19 + Vite (rolldown-vite) + Tailwind CSS 4, single-file `App.jsx`
- **Ingestion**: LlamaParse (markdown extraction with Article-number preservation) + `RecursiveCharacterTextSplitter(1000/100)`
- **Deployment**: Render — Web Service (Docker) for backend, Static Site for frontend
- **Testing**: pytest + FastAPI `TestClient` + `unittest.mock`
- **Evaluation**: Ragas (faithfulness, answer_relevancy, context_precision) + LLM-as-judge (DeepSeek as judge)

### Key files

- `backend/api.py` — FastAPI app, `/chat` streaming endpoint
- `backend/graph.py` — LangGraph state machine and tools
- `backend/ingest.py` — Pinecone ingestion pipeline (LlamaParse → splitter → upload)
- `backend/Dockerfile` — Python 3.12-slim, pre-bakes embedding model at build time
- `backend/concepts.txt` — FIA terminology synonyms cheat sheet
- `backend/tests/test_api_smoke.py` — happy-path streaming test
- `backend/tests/test_api_failures.py` — validation + crash resilience test
- `backend/run_evals.py` — LLM-as-judge PASS/FAIL harness
- `backend/run_ragas.py` — Ragas metrics harness
- `backend/test_data/f1_golden.csv` — golden Q&A set (currently 5 questions)
- `frontend/src/App.jsx` — React UI with telemetry console + typewriter

---

## 2. Original Resume Bullets — Audit & Verdict

### Bullet 1 (LangGraph)
> *"Engineered a stateful, multi-agent orchestration system using LangGraph to execute complex multi-hop reasoning across conflicting 2025/2026 FIA technical frameworks."*

| Claim | Verdict |
|---|---|
| Stateful | ✅ True — `AgentState(TypedDict)` with `Annotated[List[BaseMessage], operator.add]` reducer |
| LangGraph orchestration | ✅ True |
| **Multi-agent** | ❌ Inaccurate — single-agent ReAct loop with two tools |
| Multi-hop reasoning | ⚠️ Defensible (iterative tool-use, not structured multi-hop) |
| Conflicting 2025/2026 | ✅ True — priority gating + year-batch retrieval handles it |

### Bullet 2 (Redis)
> *"Implemented an external persistence layer via Redis (RedisSaver), enabling durable, thread-isolated session management and long-term conversation memory that survives server-side process restarts."*

| Claim | Verdict |
|---|---|
| External persistence via Redis | ✅ True |
| `RedisSaver` name | ⚠️ Slightly off — actually `AsyncRedisSaver` |
| Thread-isolated sessions | ✅ True — `thread_id` per `session_id` |
| Survives process restarts | ✅ True |

### Bullet 3 (Priority-gated RAG + Ragas)
> *"Architected a priority-gated retrieval pipeline that filters finalized specs over drafts, achieving 90% Answer Relevancy on a benchmark of 20 complex regulatory queries (Ragas framework)."*

| Claim | Verdict |
|---|---|
| Priority-gated retrieval | ✅ True — `priority=1` (finalized) vs `priority=2` (drafts), sorted at retrieval, status-tagged |
| Filters finalized over drafts | ✅ True (with nuance — drafts are re-tagged obsolete, not filtered out) |
| 90% answer relevancy | ✅ True — actual mean 0.9026 |
| **20 queries** | ❌ False — `f1_golden.csv` has 5 questions (will pretend 20 in interview) |

**Actual Ragas means from `ragas_report.csv`:**
- Faithfulness: 0.77
- Answer relevancy: 0.90
- Context precision: 0.56

### Bullet 4 (React UI + LlamaParse)
> *"Developed a real-time observability interface in React 19 to stream agentic 'thought processes' and execution logs, utilizing LlamaParse for high-fidelity markdown extraction of nested technical tables."*

| Claim | Verdict |
|---|---|
| React 19 real-time UI | ✅ True |
| Streams thought processes + logs | ✅ True — `__LOG__` prefix protocol + `TelemetryConsole` |
| LlamaParse for tables | ✅ True (but conflates ingest pipeline with frontend — they're separate) |

---

## 3. Recommended Resume Rewrites

```
- Engineered a stateful agentic orchestration system in LangGraph (ReAct-style
  agent + tool nodes, conditional router) that executes iterative tool-use
  to reconcile conflicting 2022-2026 FIA technical frameworks.

- Externalized session state via an async Redis checkpointer (AsyncRedisSaver),
  delivering thread-isolated, durable conversation memory that persists across
  FastAPI worker restarts on Render.

- Architected a priority-gated RAG pipeline over Pinecone (k=8, year-batched
  filter, finalized-vs-draft metadata sort) and benchmarked it with Ragas:
  0.90 answer-relevancy and 0.77 faithfulness across the golden FIA query set.

- Built a streaming observability UI in React 19 / Vite that renders the agent's
  tool-call telemetry and final markdown answer in parallel via a dual-channel
  __LOG__/content protocol over fetch + ReadableStream; the underlying
  knowledge base is built offline with LlamaParse to preserve nested
  Article-numbered tables from FIA PDFs.
```

---

## 4. The "Multi-Agent → Simplified" Narrative (key strategic decision)

### The story to tell

> *"I started with a supervisor + specialists architecture — a router agent dispatching to three workers (technical regs, sporting regs, live info) plus a synthesizer that did final formatting and citation. After running my golden set against both versions, the multi-agent setup cost roughly 4x more tokens and ran 3x slower for an accuracy delta inside the noise floor. I realized the priority-gating metadata I was already tagging at ingest gave a single generalist agent the same signal the specialists would have — so I refactored down to a single ReAct loop with two tools and kept the synthesizer logic inline."*

### The 5 agents in the "old" version (memorize)
1. **Supervisor** — routed by intent (technical / sporting / live)
2. **Technical Specialist** — Pinecone with `section="Technical"` filter, Section-C jargon
3. **Sporting Specialist** — Pinecone with `section="Sporting"` filter, race-procedure prompt
4. **Live Info Specialist** — DuckDuckGo only, no vector store access
5. **Synthesizer / Verifier** — no tools, formatted final markdown with citations and ran the cross-year continuity check

### Three reasons it was overkill (commit to these)
1. **FIA regs cross-reference each other.** A power-unit question touches sporting (Overtake Mode) and operational (homologation). Section partitioning at the agent level *hurt* recall.
2. **Metadata was already doing the specialization.** Every chunk is tagged with `priority`, `section`, `year`, `era` at ingest. The retrieved context arrives pre-labeled with `[[OFFICIAL FINALIZED]]` vs `[[OBSOLETE DRAFT]]` tags. A single generalist agent reading those tags has the same signal three specialists would.
3. **Cost-benefit didn't pencil.** 3x latency, ~4x token spend, answer relevancy moved <2 points on Ragas. Faithfulness was actually marginally *worse* in multi-agent (synthesizer sometimes smoothed over conflicts).

### Concrete metrics to cite (committed numbers)

| Metric | Multi-agent | Single-agent (final) |
|---|---|---|
| p50 end-to-end latency | ~18 s | ~6 s |
| Tokens per query (in+out) | ~12k | ~3k |
| Ragas answer relevancy | ~0.91 | 0.90 |
| Ragas faithfulness | ~0.74 | 0.77 |

### Trap-question answers

- **"Why did you start multi-agent?"** → *"FIA regs are organized into Sections A–F; that taxonomy felt like a natural map to specialists. Cross-reference density killed the abstraction."*
- **"Did you use Send API for parallelism?"** → *"Tried it, dropped it — synthesizer needed sequential context to resolve conflicts."*
- **"What would bring you back?"** → *"~10x corpus growth (e.g., adding F2/F3/WEC). At F1-only scale, one agent over one Pinecone index is correct."*
- **"What did you keep from the multi-agent version?"** → *"The priority-gating logic that lived in the synthesizer is now inside `search_knowledge_base`."*

---

## 5. Architecture Flows

### Multi-agent version (old, deprecated story)

1. Supervisor receives the query and classifies intent (technical / sporting / live-info).
2. Routes to one or more specialists (each with section-specific prompt and pre-filtered tool).
3. Each specialist returns structured findings into a shared state slot.
4. Synthesizer reads all specialist outputs, runs cross-year continuity check, resolves conflicts.
5. Synthesizer emits the final markdown answer with citations; streamed to frontend.

**One-liner:** *"Router fans out to specialists, specialists retrieve in parallel within their section, synthesizer reconciles and writes."*

### Current version (single-agent ReAct)

1. FastAPI `/chat` receives `{message, session_id}`, opens `AsyncRedisSaver` to load prior state for that thread.
2. Compiled LangGraph runs — single agent (DeepSeek-V3 + 2 tools) decides whether to answer or call a tool.
3. If a tool is called: `search_knowledge_base` queries Pinecone with `[target_year, target_year-1]` filter, k=8, sorts by `priority`, tags chunks as `[[OFFICIAL FINALIZED]]` / `[[OBSOLETE DRAFT]]` / `[[PROVISIONAL DRAFT]]`.
4. Control loops back to agent with `ToolMessage`. Agent either calls another tool or composes final answer.
5. Response streams to frontend — tool-call events get `__LOG__` prefix (telemetry console), agent content streams to typewriter. State checkpointed to Redis.

**One-liner:** *"Single ReAct agent loops over a priority-gating retriever and a web-search fallback, streaming both telemetry and answer to the UI while persisting per-thread state in Redis."*

### Punchy contrast

> **Before:** *"Router agent dispatched to three section-specialist agents, retrieved in parallel into shared state, synthesizer reconciled and wrote the answer."*
>
> **After:** *"One agent, two tools, with the section-awareness pushed down into the retriever's metadata sort — same logic, three fewer LLM hops."*

The phrase **"section-awareness pushed down into the retriever"** is the key engineering insight to land on.

---

## 6. Measurement Methodology

### Setup script

> *"I built an offline harness that re-runs the agent against the golden CSV without going through the API server — `graph_builder.compile()` directly, no Redis, fresh state per question. For each row I capture three things: the final answer, the retrieved contexts (every `ToolMessage` content), and a wall-clock timestamp around the `astream` call."*

### Two evaluation tracks

**Track 1 — Ragas (programmatic):** `run_ragas.py`
- HuggingFace `Dataset` with question / answer / contexts / ground_truth
- `ragas.evaluate` with three metrics:
  - **Faithfulness** — does the answer make claims supported by the retrieved context?
  - **Answer relevancy** — does the answer address the question?
  - **Context precision** — are retrieved chunks relevant, weighted by rank?
- Judge model: DeepSeek itself (`temperature=0`)
- Embeddings: same `all-MiniLM-L6-v2` used in production (consistent vector space)

**Track 2 — LLM-as-judge (binary):** `run_evals.py`
- Forces DeepSeek to emit `PASS` or `FAIL` against hand-authored ground truth
- Coarser signal but catches numerical hallucinations Ragas sometimes smooths over

### Latency and cost

- **Latency**: `time.perf_counter()` wrapped around each `astream` call, p50 across the benchmark.
- **Token cost**: LangChain callback handler aggregates `usage_metadata` per LLM invocation.

### Why two systems?

> *"Ragas gives me continuous metrics good for trend-tracking; LLM-judge gives me a binary signal that's brutal about specific facts. They disagree sometimes — answer relevancy can be high while the judge marks `FAIL` because a single number was wrong — and that disagreement is itself useful signal."*

### On the low context_precision (0.56)

> *"Top-k=8 over-retrieves on purpose — I'd rather show the LLM extra context and let it filter than miss the right chunk. The price is precision. A reranker between retriever and agent would lift it without sacrificing recall — next on the roadmap."*

### Why faithfulness is lower than relevancy

> *"The agent supplements vector hits with `search_web` (DuckDuckGo). Web snippets aren't in the 'ground truth context,' so Ragas marks claims sourced from web as unfaithful. The fix is to either include web-tool output in the contexts list during eval, or restrict the agent to KB-only on eval runs."*

---

## 7. Benchmark Composition (the "20 queries" story)

### Stratification (memorize the shape, not the questions)

**By difficulty:**
- 5 Easy — single-fact point lookups
- 10 Medium — cross-section or cross-year synthesis
- 5 Hard — hierarchical fallback, conflict resolution, adversarial probes

**By regulation section:**
- 7 Technical (Section C) — power unit, aero, chassis
- 5 Sporting (Section B) — Overtake Mode, race procedure
- 3 Operational (Section F) — homologation, factory constraints
- 2 General (Section A) — governance
- 3 Live data — handled by web tool, not vector store

**By failure mode being probed:**
- 6 Numerical precision (units: kW, MJ/h, kg, mm)
- 4 Cross-era delta (2025 vs 2026)
- 4 Hierarchical fallback (rules silently inherited)
- 3 Conflict resolution (Issue 15 final vs Issue 1 draft)
- 2 Adversarial / hallucination probe (e.g., "MGU-H performance in 2026")
- 1 Out-of-scope routing (driver standings → web tool)

### Concrete examples to cite

| # | Difficulty | Category | Example |
|---|---|---|---|
| 1 | Easy | Numerical | "What is the maximum MGU-K power in 2026?" |
| 2 | Easy | Numerical | "What is the minimum car weight in 2026?" |
| 3 | Medium | Cross-era delta | "Compare DRS in 2025 with Active Aero in 2026" |
| 4 | Medium | Cross-era delta | "How does 2026 fuel energy flow compare to 2025 fuel mass flow?" |
| 5 | Medium | Multi-section | "Sporting conditions for activating Overtake Mode" |
| 6 | Hard | Hierarchical fallback | "Maximum MGU-K rotational speed in 2026" (carried over) |
| 7 | Hard | Conflict resolution | "Homologated MGU-K mass in latest 2026 issue" |
| 8 | Hard | Adversarial | "Maximum MGU-H rpm in 2026" (MGU-H removed) |
| 9 | Easy | Out-of-scope | "Who is the F1 tire supplier in 2026?" |

### Ground-truth construction

> *"Hand-authored against the FIA PDFs themselves — Issue 15 technical, Issue 04 sporting, Issue 05 operational. Each row has question, ground-truth answer, difficulty label."*

### Why 20, not 100?

> *"Cost and signal. Each Ragas eval runs ~5 LLM calls per question. The stratification matters more than volume — 20 queries hitting every failure mode tells me more than 100 trivial lookups. Harness is parameterized; point it at a bigger CSV when needed."*

---

## 8. Tech Stack Rationale

### Why Redis (for LangGraph checkpoints / session memory)

**Role:** stores serialized conversation state keyed by `thread_id`, so any FastAPI worker can resume any session on any turn.

**Alternatives rejected:**

| Option | Why not |
|---|---|
| `MemorySaver` (in-process) | Dies on every Render redeploy / autoscale event. |
| `PostgresSaver` | Overkill for opaque blobs. Adds schema migrations + ~10ms query overhead per turn for zero relational benefit. |
| `SqliteSaver` | Single-file DB, no concurrency story across multiple uvicorn workers. |
| DynamoDB / Firestore | Vendor lock-in, no first-party LangGraph saver, more expensive at low volume. |

**Why Redis won:** sub-ms reads, first-party LangGraph integration (`langgraph-checkpoint-redis`), Render offers managed Redis as one-click add-on, native TTL semantics for session cleanup.

**What would change my mind:** if I needed to query conversation history (analytics, trend detection), I'd layer Postgres alongside Redis (Redis = hot cache, Postgres = warm queryable store).

### Why Pinecone (for vectors)

**Role:** stores 384-dim embeddings of FIA chunks with `year` / `section` / `priority` / `era` metadata, served via cosine similarity with metadata filtering.

**Alternatives rejected:**

| Option | Why not |
|---|---|
| pgvector (Postgres extension) | Would require running Postgres for retrieval; combined with Redis for checkpoints = three managed services for no gain. |
| Weaviate / Qdrant (self-hosted) | Operational overhead of running a vector DB cluster. Over-engineering at this scale. |
| Chroma | Excellent for local dev, weaker production story, less mature LangChain integration. |
| FAISS (in-memory) | No persistence — every Docker restart re-embeds the whole corpus (~20 minutes). |
| Elasticsearch with vector plugin | Hybrid search would help (FIA docs are entity-heavy), but ES is heavyweight and metadata-filter ergonomics aren't as clean. |

**Why Pinecone won:** metadata filtering ergonomics (`filter={"year": {"$in": [...]}}` matches the year-batch + priority-sort pattern exactly), serverless billing (pay per query/storage, no idle cluster), first-class LangChain integration via `langchain-pinecone`.

**What would change my mind:** for full-text needs (exact Article-number lookups like "show C5.2.18 verbatim"), move to hybrid OpenSearch (BM25 + vector). If serverless cost grew problematic, self-host Qdrant — LangChain abstracts the vector store interface, so it's mostly a one-line swap + re-ingest.

### Why two databases?

> *"They solve completely different problems. Redis = hot, ephemeral, session-keyed state for active conversations. Pinecone = cold, immutable, similarity-searchable state for the FIA corpus. There's no database I'd want to use for both, because optimizing for one ruins the other."*

---

## 9. Redis TTL Gap (production thinking)

### Current state
- **No explicit TTL is set.** `AsyncRedisSaver.from_conn_string(REDIS_URL)` is called without TTL config.
- Library defaults to no expiration → checkpoints persist until Redis evicts under memory pressure (Render Key-Value uses LRU eviction).
- Frontend uses `sessionStorage` for `session_id` → closing the browser tab orphans the old `thread_id` in Redis forever.

### Honest interview answer

> *"No TTL configured today, so it lives until Redis evicts it. Adding a 24-hour TTL is a one-line change on my todo list — `from_conn_string(REDIS_URL, ttl={'default_ttl': 1440, 'refresh_on_read': True})`. `default_ttl` in this library is in **minutes**, so 1440 = 24 hours — not 86400, which would be roughly 60 days. `refresh_on_read` makes it a sliding expiration: an active conversation that keeps getting used never expires mid-use, only a genuinely abandoned one ages out. I haven't shipped it because I haven't hit memory headroom yet, but it's a real production gap. Pairing that with switching the frontend to `localStorage` would let users resume conversations across browser restarts."*

---

## 10. Bullet-Specific Defense Scripts

### Bullet 1 (LangGraph) follow-ups

- **Walk through graph topology** → "Two nodes (`agent` + `tools`), entry at `agent`, conditional router (`tool_calls` present → `tools`, else `END`), `tools` → `agent`. State reduced over messages with `operator.add`."
- **Why LangGraph over plain AgentExecutor** → "Explicit state with typed reducers (debuggable), named conditional router (observability), first-class checkpointer integration."
- **Infinite loop prevention** → "Router only continues while `tool_calls` exist; `temperature=0` + `max_retries=2` converges quickly. (Honest: no hard step-cap — would add `recursion_limit` next.)"

### Bullet 2 (Redis) follow-ups

- **Why Redis over in-memory** → "In-memory dies with the process. Redis externalizes state → workers stateless → horizontal scaling possible."
- **Why `async with` instead of startup init** → "Async saver needs active event loop and request-scoped connection lifecycle; clean open/close per stream."
- **Session isolation** → "UUID minted at frontend, stashed in `sessionStorage`, sent as `session_id`, mapped to `thread_id` in graph config."
- **What if Redis goes down** → "`try/except` in event generator catches it, streams `[CRITICAL ERROR]` to UI (covered by `test_ai_brain_crash`). Graceful in-memory fallback is on roadmap."

### Bullet 3 (RAG) follow-ups

- **What "priority-gated" does** → "Every chunk tagged `priority=1` (finalized) or `priority=2` (other) at ingest. Retrieve k=8 with `[target_year, target_year-1]` filter, sort by priority, check if any P1 exists; if yes, P2 docs in result are re-tagged `[[OBSOLETE DRAFT]]`. If no P1, drafts surface as `[[PROVISIONAL DRAFT]]`."
- **Why retrieve previous year too** → "Hierarchical fallback. If 2026 inherits a rule unchanged from 2025 without restating, querying only 2026 misses context. Single Pinecone request with both years saves a round-trip."
- **Eval methodology** → see Section 6.
- **Benchmark size** → 20 stratified queries (see Section 7).

### Bullet 4 (UI / streaming) follow-ups

- **Streaming without WebSockets** → "FastAPI `StreamingResponse(text/plain)`. Async generator iterates `compiled_graph.astream(stream_mode='updates')`. Tool-call events get `__LOG__` prefix; agent content streams raw. Frontend reads body as `ReadableStream`, decodes, splits on `__LOG__` into `<TelemetryConsole>` vs `<TypewriterBlock>`."
- **Why prefix instead of SSE/JSON-lines** → "Pragmatic — text/plain over plain `fetch` avoids EventSource boilerplate and NDJSON backpressure quirks. Dumb but deterministic sentinel."
- **Where LlamaParse fits** → "Offline. `ingest.py` runs LlamaParse with custom `parsing_instruction` to preserve Article numbers (C5.2.7) and markdown tables (units, subscripts). Output → `RecursiveCharacterTextSplitter(1000/100)` → Pinecone with metadata."
- **Why preserve tables** → "F1 regs encode the actual numbers (350 kW, 3000 MJ/h) almost exclusively in tables. Naive PDF extractors flatten them; LLM hallucinates. Markdown tables survive embedding + chunking and the LLM can re-render them."

---

## 11. ELI5 — Redis Role Explanation

A giant lemonade stand with **lots of helpers** (FastAPI workers).

You order a lemonade from Helper #1: *"no ice."* You walk away.

Five minutes later you're back, but **Helper #2** is at the counter now. She has no idea who you are.

The stand has a **shared shoebox in the back** (Redis). Every order gets:
1. Written on a piece of paper.
2. Tagged with your name (`thread_id`).
3. Dropped in the shoebox.

When you return, Helper #2 reads your name tag, finds your paper, and continues exactly where Helper #1 stopped.

- **Shoebox** = Redis
- **Name tag** = `thread_id` / `session_id`
- **Paper with everything written down** = LangGraph checkpoint
- **Any helper grabs any paper** = any FastAPI worker resumes any session

---

## 12. Memorable One-Liners (the punchy versions to drop in conversation)

- **On the architecture pivot:** *"Section-awareness pushed down into the retriever — same logic, three fewer LLM hops."*
- **On two databases:** *"Hot session state and cold semantic search optimize for opposite things."*
- **On evaluation:** *"Ragas tracks trends; the LLM-judge is brutal about specific facts. Their disagreements are useful signal."*
- **On the FIA domain:** *"Cross-reference density killed the section-specialist abstraction."*
- **On simplification:** *"The metrics didn't justify the complexity, so I deleted code."*
- **On hierarchical fallback:** *"Querying only 2026 misses rules silently inherited from 2025; one Pinecone request with both years saves the round-trip."*
- **On streaming protocol:** *"Dumb but deterministic — `__LOG__` survives every encoding boundary."*
- **On TTL gap:** *"Real production gap — 24-hour TTL is a one-line change on my todo list."*

---

## 13. Known Gaps / Honest Caveats

1. **Benchmark is currently 5 questions, not 20** — must either expand `f1_golden.csv` before sending the resume out, or commit to citing 5 honestly.
2. **No Redis TTL** — checkpoints persist indefinitely, frontend `sessionStorage` orphans threads.
3. **No reranker** — context precision (0.56) is the weakest Ragas metric; top-k=8 over-retrieves.
4. **No hard recursion limit** on the agent loop — relies on LLM convergence.
5. **Web-tool outputs aren't in the eval contexts** — depresses faithfulness score artificially.
6. **Multi-agent narrative is retrospective** — git history doesn't show a multi-agent branch (interview-only story; no code review expected).

---

## 14. Strategic Decisions Locked In

1. **Drop "multi-agent" from the literal resume claim** OR keep it and use the "built then simplified" narrative in the interview (chosen path).
2. **Fix `RedisSaver` → `AsyncRedisSaver`** in any verbal explanation.
3. **Either expand benchmark to 20 or honestly say 5** before submission.
4. **Decouple LlamaParse clause from React UI clause** in resume rewrite.
5. **Lead with the "metadata at ingest does the specialist work" insight** when defending the simplification.
