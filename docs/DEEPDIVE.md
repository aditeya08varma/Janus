# Janus 2.0: The Deep Dive

*A record of how it was actually built, what was actually wrong with it, and how it actually got fixed. Written so I stop re-explaining this from memory every time someone asks.*

---

## Why Janus exists

Formula 1 rewrote its technical regulations for 2026. Minimum weight drops from 800kg to 770kg. MGU-H — the piece of the power unit that recovers heat energy from the turbo — gets deleted outright. DRS, the single push-to-pass button drivers have had since 2011, is replaced by "Active Aero," a two-mode aerodynamic system (X-Mode for straights, Z-Mode for corners) that behaves completely differently. It's not a rules update, it's a different car built to a different philosophy, and the actual specification of that difference lives in FIA PDFs: hundreds of pages of Article-numbered technical and sporting regulations, most of the content that matters buried in tables.

I wanted a tool that could answer precise questions against that document set — "what's the maximum MGU-K power in 2026," "how does 2026 fuel-flow compare to 2025," "under what conditions can a driver use Overtake Mode" — and cite the actual regulation it pulled the answer from, rather than confidently guessing. That's the whole premise: a RAG chatbot where "I don't know" is an acceptable answer and a hallucinated number is not.

The name is deliberate. Janus is the Roman god of transitions, one face looking backward, one forward — the project sits exactly on the 2025→2026 rules boundary and has to reason about both eras at once, including regulations that carry over from 2025 without being restated in 2026 documents. Internally it's split into "Hugin" (the DeepSeek-V3 reasoning model, thought) and "Munin" (the Pinecone vector store, memory) — Odin's ravens, because once you've picked one mythology theme you might as well finish it.

---

## What it actually is

A single LangGraph agent (DeepSeek-V3) with two tools — a Pinecone-backed regulation search and a DuckDuckGo web search for anything outside the FIA document set — sitting behind a FastAPI streaming endpoint, talking to a React frontend styled like a mission-control telemetry feed. Session state (which messages belong to which conversation) is checkpointed to Redis so a page refresh doesn't lose your conversation.

That's it. It is not a multi-agent system, it doesn't have a supervisor routing to specialists, and if you go looking for that in the commit history you won't find it — it was never built that way. It's one ReAct loop: the model decides whether it has enough information to answer, and if not, calls a tool, reads the result, and decides again. The "specialization" happens at the data layer instead: every chunk in the vector store is tagged at ingest time with a year, a section (Technical/Sporting/Operational/General), and a priority (finalized regulation vs. draft), and the retriever uses those tags to filter and sort. The agent doesn't need to be smart about which specialist to consult — the data already tells it which years and priorities matter.

```
Browser (React 19 / Vite)
  │  POST /chat {message, session_id}
  ▼
FastAPI /chat (backend/api.py)
  │  Redis checkpointer + compiled graph, both opened once at startup
  ▼
LangGraph StateGraph (backend/graph.py)
      ┌────────────┐   tool_calls?   ┌───────────┐
      │   agent    │ ─────────────▶  │   tools   │
      │ DeepSeek-V3│ ◀─────────────  │  (async,  │
      └────────────┘   ToolMessage   │  parallel)│
                                      └───────────┘
        │                                  │
        │                        ┌─────────┴─────────┐
        │                 search_knowledge_base   search_web
        │                 (Pinecone, year + priority   (DuckDuckGo,
        │                  filtered, cross-encoder      live/off-domain
        │                  reranked)                    facts)
        ▼
  astream(stream_mode=["updates","messages"])
        │  tool-call events → "__LOG__..." lines
        │  answer tokens    → streamed live, as generated
        ▼
  StreamingResponse(text/plain) → Browser splits __LOG__ lines
        into the telemetry console vs. the typewriter answer
```

---

## The problem I actually went looking for: "it's too slow"

The symptom was simple to describe and annoying to live with: ask Janus a comparison question — "compare 2025 DRS with 2026 Active Aero" — and watch the telemetry console print three or four `ANALYZING... REGS` lines almost instantly, then just sit there for fifteen to thirty seconds before the answer showed up all at once. Not a slow trickle of text. A wall of silence, then the whole answer, rendered by a client-side typewriter effect that made it *look* like it was streaming in real time, which was actively misleading about where the time was actually going.

I went into `backend/api.py` and `backend/graph.py` assuming I'd find one obvious bottleneck. Instead I found five separate things stacking on top of each other, none of which was individually dramatic, all of which compounded on exactly the query shape — a multi-year comparison — that was in the demo screenshot.

### 1. There was no real token streaming

`api.py`'s `/chat` endpoint was calling `graph.astream(inputs, config, stream_mode="updates")`. That mode is deceptively named — "updates" doesn't mean "as things update," it means "once per node completion." The LLM call inside the agent node was `llm_with_tools.invoke(...)`, a synchronous, non-streaming call. So the entire DeepSeek-V3 response — the full answer, generated token by token on DeepSeek's servers — had to finish completely before LangGraph would emit anything for that node. The backend genuinely had nothing to send until the whole answer existed. React's `useTypewriter` hook then replayed that already-complete string one character at a time via `setInterval`, which is why it looked like streaming from a distance and clearly wasn't up close: the text was appearing at a fixed rate that had nothing to do with generation speed, starting from a dead stop.

**Fix:** switch to `stream_mode=["updates", "messages"]`, which multiplexes two channels — structural events (a tool was called, a tool finished) still come through `"updates"`, but the `"messages"` channel emits every token the model generates, the moment it generates it, as long as the node invoking the model is `async` (`agent_node` is now `await llm_with_tools.ainvoke(...)` instead of a blocking `.invoke()`). The frontend now gets real tokens in real time; the typewriter is still there for texture, but it's animating something that's actually arriving live, not replaying a variable that was already fully populated.

### 2. Tool calls ran one at a time, on purpose, by accident

The system prompt tells the model: *"if a YEAR HINT is present, pass those years as target_year (call the tool once per year when comparing)."* That's correct behavior — a 2025-vs-2026 question genuinely needs two separate, differently-filtered Pinecone searches. But `tool_node` executed them with a plain Python `for` loop:

```python
for tool_call in last_message.tool_calls:
    ...
    res = selected_tool.invoke(tool_call["args"])
```

Two searches that have zero dependency on each other were being run sequentially, each one blocking the next. That's exactly the "3× `ANALYZING`" burst in the telemetry log, followed by one long wait that was actually two (or three) searches' worth of latency added end to end instead of overlapped.

**Fix:** the loop is now `asyncio.gather` over an async wrapper that runs each tool call in a thread (`asyncio.to_thread`), since the underlying search functions are still synchronous:

```python
async def tool_node(state: AgentState):
    last_message = state["messages"][-1]
    outputs = await asyncio.gather(*(_run_tool_call(tc) for tc in last_message.tool_calls))
    return {"messages": list(outputs)}
```

N tool calls now cost roughly the time of the *slowest one*, not the sum of all of them.

### 3. Every search call did real CPU work, not just a network request

This was the one I didn't expect going in. `search_knowledge_base` looks like it should be "embed the query, hit Pinecone, done" — a couple of network round trips. It's actually running two local machine-learning models on every single call:

- `HuggingFaceEmbeddings("all-MiniLM-L6-v2")` — turns the query into a 384-dimension vector, a real forward pass through a sentence-transformer model, done on CPU, before Pinecone is ever contacted.
- A `CrossEncoder` reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`) — after Pinecone returns up to 24 candidate chunks, this model scores every (query, chunk) pair to pick the best 8. Also a real CPU forward pass, also per call.

Both models were instantiated lazily — as module-level globals, created on first use — which meant the very first search after any deploy or restart also paid full model-load time on top of everything else, silently, on some unlucky user's first query.

**Fix:** `graph.warm_dependencies()` now runs during FastAPI's `lifespan` startup, forcing both models to load once, at boot, before any request can hit them — the same place the graph gets compiled and Redis gets connected. The per-call cost doesn't go away (each search is still a real embedding + a real rerank), but it no longer stacks a cold model load onto whichever user happens to ask the first question.

### 4. The cascade fallback can multiply all of the above again

If a search's best match scores below a confidence threshold, `retrieve_with_cascade` widens the year window and searches again — up to three more times, fully sequentially, each one paying its own embedding + Pinecone + rerank cost. This is genuinely correct behavior (it's how a question about a rule "inherited" from two years back still finds it), but it means a single weak match can silently turn one tool call into up to four.

This one I left alone. It's a real, deliberate accuracy/latency trade-off, not a bug — see the "Known gaps" section for where I'd actually pull on this thread if the extra latency became a problem.

### 5. Two full model round-trips is the floor, not the ceiling

A ReAct loop means: agent decides to call a tool → tool runs → agent sees the result → agent decides again. Minimum two calls to DeepSeek's API per turn, each a real network round trip, with no client-side timeout configured — only `max_retries=2`. A slow or hung DeepSeek response had no ceiling on how long it could hang a request.

**Fix:** added `timeout=30` to the `ChatDeepSeek` client. It's a floor under the failure mode, not a speed improvement, but a request that used to be able to hang indefinitely now fails predictably.

### What that adds up to

A single "compare 2025 vs 2026" question, before any of this: 2 embeddings + 2 Pinecone queries + 2 reranks + 2 LLM calls, every one of them serialized, none of them streamed. That's the 15–30 second gap in the telemetry log, exactly. After: the two searches overlap instead of stacking, the models doing the searching are already warm, and the answer itself starts appearing the moment DeepSeek starts producing it instead of after it finishes. None of these fixes touch accuracy — they're strictly about not paying the same latency bill twice when there was no reason to.

---

## The other thing I found while I was in there

Along the way, three things that weren't about speed but were worth fixing while the files were open:

- **The `/chat` endpoint's catch-all error handler was echoing raw exception text into the response stream.** `except Exception as e: yield f"...[CRITICAL ERROR: {str(e)}]"` — whatever internal error message Pinecone or DeepSeek's client library produced was going straight to the browser. Now it's logged server-side in full and the client gets a generic message.
- **`backend/ingest.py` had about 160 lines of dead, fully-commented-out code sitting at the top of the file** — an earlier draft of the ingestion script, left in place after the real one was written below it. Deleted; git history still has it if it's ever needed.
- **The project's own `.venv` was missing about a third of the packages in `requirements.txt`** — `langchain-pinecone`, `sentence-transformers`, `pinecone`, `llama-parse`, `nest_asyncio`, and a few others. This didn't break anything visibly because the modules that needed them are imported lazily inside functions that only run on a real search call — so `import graph` succeeds fine, and it isn't until someone actually asks a question that requires Pinecone that the environment's staleness becomes visible. Worth a `pip install -r requirements.txt` sanity check periodically, since a partially-installed venv fails silently until the exact code path that needs the missing package runs.

---

## The retrieval-correctness pass (the one before this one)

This deep-dive is about the second pass through this codebase. There was an earlier one, in August 2026 (`docs/BUGFIXES.md` has the full list), that fixed a different category of problem: not "slow," but "confidently wrong." Worth summarizing here because it's the reason the retrieval pipeline looks the way it does:

- The synonym glossary (`concepts.txt`, tagged `year=0` at ingest) was **permanently unreachable** — every search filtered on `year ∈ {target_year, target_year-1}`, and `0` never matched either. It sat in the index, cost storage, and never once got retrieved. Fixed with an `$or` filter that always includes `source = CheatSheet` regardless of year.
- Same-priority chunks from different years (both "finalized," one from 2025 and one from 2026) were sorted by Pinecone's raw similarity order, which meant a superseded number could out-rank the current one purely because the user's phrasing happened to be closer to the old paragraph. Fixed with an explicit `(priority, -year)` sort key and a `[[CROSS-YEAR CONFLICT]]` banner injected into context whenever two finalized years show up together.
- The retrieval window was hardcoded to the target year and one year back. A rule last restated three years prior — genuinely common, since FIA regs don't repeat unchanged clauses every issue — returned nothing. Fixed with the score-gated cascade described above: only widen the search when the best match is weak, capped at three extra years.
- Chunking split markdown tables from their header rows, so a retrieved chunk could contain `770 | 350 | REMOVED` with no column labels — numbers with no schema, a direct path to a hallucinated unit. Fixed with `chunking.py`'s table-aware splitter, which detects markdown table blocks and repeats the header on every piece if a table has to be split. **This fix shipped in code in August but the live Pinecone index was never re-ingested against it** — the old, non-table-aware chunks were still what the system was actually searching until the re-ingest that accompanied this deep-dive.
- `ingest.py` used to wipe the entire Pinecone index on every run (`delete_index` + `create_index`), which meant a failed run after the delete left production with an empty index, and just *importing* the ingest module was enough to trigger it. Fixed with an incremental default path (delete-then-upsert per source file, deterministic chunk IDs) and a `--fresh` flag for the rare case a full rebuild is actually wanted.
- `/chat` had no auth and no rate limit — `curl` could hit it as many times as it wanted, and every hit costs a Pinecone query, an embedding, a rerank, and a DeepSeek call. Fixed with an optional `X-API-Key` header check and a 20-requests-per-minute-per-IP limiter.

Full detail, including the exact before/after code and the reasoning for every trade-off that was *kept* on purpose, is in `docs/BUGFIXES.md` — that document is written as a reference, not a narrative, so it's the place to go for "why does the code do it this specific way" rather than "why did I go looking."

---

## Technical specifications, for reference

| Layer | Choice | Why |
|---|---|---|
| LLM | DeepSeek-V3 via `langchain-deepseek`, `temperature=0` | Deterministic-leaning output for a domain where "close enough" is a failure; `temperature=0` isn't a guarantee of determinism but it meaningfully reduces variance run to run. |
| Vector DB | Pinecone serverless, 384-dim, cosine, index `f1-regulations-all` | Metadata-filter ergonomics (`filter={"year": {"$in": [...]}}`) map directly onto the year/priority-gating this domain needs; serverless billing means no idle cluster cost between demo sessions. |
| Embeddings | `all-MiniLM-L6-v2` (HuggingFace, local) | Small enough to run on a CPU-only Render dyno with no per-call API cost or added network hop; the trade-off is lower semantic resolution than a hosted large embedding model, acceptable for a narrow, jargon-heavy domain. |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2`, optional (`ENABLE_RERANKER`) | Bi-encoder retrieval (embed query and doc separately) is fast but approximate; scoring the top ~24 candidates jointly with a cross-encoder before truncating to 8 raises precision without re-running the expensive part (full-corpus search) twice. |
| Session memory | Redis via `AsyncRedisSaver` (LangGraph checkpointer), 24h sliding TTL | Sub-millisecond reads, first-party LangGraph integration, native TTL for automatic cleanup of abandoned conversations — the alternative (Postgres, DynamoDB) all add either schema overhead or per-query latency for what's fundamentally an opaque blob keyed by thread ID. |
| Orchestration | LangGraph `StateGraph`, 2 nodes (`agent`, `tools`), 1 conditional router | Explicit, typed state (`Annotated[List[BaseMessage], operator.add]`) and a named router are easier to reason about and debug than an implicit agent-executor loop; a `recursion_limit=10` caps runaway tool-calling. |
| Backend | FastAPI, `StreamingResponse(media_type="text/plain")` | Plain `fetch` + `ReadableStream` on the frontend avoids `EventSource`/SSE boilerplate; a `__LOG__`-prefixed-line protocol is a deliberately dumb but deterministic way to multiplex two logical streams (telemetry, answer) over one text/plain body. |
| Frontend | React 19 + Vite + Tailwind CSS 4 | Single-file `App.jsx`; a `MutationObserver`-driven auto-scroll and a client-side typewriter animation give the interface its "mission control" feel independent of how the data actually arrives underneath. |

---

## Known gaps — the honest list

- **`DuckDuckGoSearchRun`** (the `search_web` tool, for anything outside the FIA document set — driver standings, live news) is a scraping-based library with no SLA, prone to rate-limiting. It's marked "mandatory for live news" in the system prompt but is the least reliable dependency in the stack. A paid search API would fix this at the cost of a monthly bill for a portfolio project.
- **The cascade fallback (latency issue #4 above) is unbounded in the worst case**: a genuinely weak query can trigger up to 4 sequential Pinecone + embedding + rerank round trips inside a single tool call, and those *aren't* parallelized the way the top-level multi-year tool calls now are, because each cascade step depends on the score of the previous one. If this becomes a real bottleneck, the next lever is lowering `CASCADE_MAX_DEPTH` or `CASCADE_SCORE_THRESHOLD` in `config.py`, at some accuracy cost.
- **In-process rate limiting** (`_rate_hits`, a dict keyed by IP) never evicts entries for IPs that stop sending requests — bounded by however many unique IPs ever hit the service, which is low-risk at current traffic but wouldn't survive horizontal scaling (each instance would rate-limit independently, with no shared state).
- **No hard retry/backoff strategy distinguishes a transient DeepSeek hiccup from a real failure** — `max_retries=2` is uniform regardless of error type.
- **The re-ingest needed to actually apply the table-aware chunking fix hadn't happened** until this pass — a reminder that a fix landing in `chunking.py` doesn't take effect in production until the vector index is rebuilt against it. `ingest.py` also has no caching between the "parse" and "chunk" steps, so any pass through it re-runs LlamaParse against every source PDF even when only the chunking logic changed downstream — expensive and slow for what should be a cheap re-chunk.
