# Janus 2.0 — Interview Question Bank

Companion to `summary.md` (which covers resume-bullet defense and eval methodology). This doc goes wider: general CS/system-design/ML-infra questions a top-tier interviewer could ask, anchored to specific lines in this codebase, plus the honest weak spots. Each question has a model answer and likely follow-ups. Read `summary.md` first for the narrative; use this for depth.

---

## 1. Architecture — the 60-second whiteboard version

```
Browser (React 19)
  │ POST /chat {message, session_id}
  ▼
FastAPI /chat (api.py)
  │ opens AsyncRedisSaver(REDIS_URL) → compiles LangGraph with that checkpointer
  ▼
LangGraph StateGraph (graph.py)
  agent node (DeepSeek-V3 + bind_tools) ──tool_calls?──▶ tools node
       ▲                                                     │
       └─────────────────────────────────────────────────────┘
  router_function: tool_calls present → "tools", else → END
  │
  ├─ search_knowledge_base(query, target_year=2026)
  │     Pinecone.similarity_search(k=8, filter={year: $in [target_year, target_year-1]})
  │     sort by priority (1=finalized first), tag OFFICIAL/OBSOLETE/PROVISIONAL
  │
  └─ search_web(query)  → DuckDuckGoSearchRun, appends "F1 2026"
  │
  ▼
astream(stream_mode="updates") → StreamingResponse(text/plain)
  tool-call events prefixed "__LOG__", agent content streamed raw
  ▼
Frontend splits on "__LOG__" per line → TelemetryConsole vs TypewriterBlock
```

Be able to draw this from memory in under a minute. Interviewers weight this heavily — it signals you actually built it rather than copy-pasted a tutorial.

---

## 2. RAG & Information Retrieval

**Q: Walk me through what happens between a user's question and the chunks that hit the LLM's context window.**
Query text → HuggingFace `all-MiniLM-L6-v2` embeds it to a 384-dim vector → Pinecone cosine-similarity search, `k=8`, filtered to `metadata.year ∈ {target_year, target_year-1}` → results sorted so `priority=1` (finalized regs) sort first → each chunk gets a status tag (`OFFICIAL FINALIZED` / `OBSOLETE DRAFT` / `PROVISIONAL DRAFT`) prepended → joined into one string, returned as a `ToolMessage`, appended to conversation state → next `agent_node` call sees it in `messages`.

**Q: Why `all-MiniLM-L6-v2` and not a larger embedding model (e.g. `text-embedding-3-large`)?**
It's a 384-dim sentence-transformer, small enough to run locally (no API cost/latency per embed call), and it's the model pre-baked into the Docker image at build time specifically to avoid a cold-start download. Trade-off: lower semantic resolution than a large hosted embedding model — for a narrow, jargon-heavy domain (FIA regs) that's a reasonable trade, but if recall were failing on paraphrased queries, a larger embedding model (or a domain-tuned one) would be the first lever to pull. Note embeddings must match between ingest and query time — same model in `ingest.py` and `graph.py` — a version mismatch there silently corrupts retrieval.

**Q: Why metadata filtering (`year IN [...]`) instead of just relying on semantic similarity to sort it out?**
Semantic similarity alone can't express "prefer the newer document" — a 2022 chunk and a 2026 chunk about "minimum weight" can be equally similar to the query embedding. The domain has an explicit temporal/priority hierarchy (finalized > draft, current year > previous year unless comparing), so that structure is pushed into metadata at ingest time and enforced with a hard filter + sort, not left to the embedding space to encode implicitly.

**Q: What's `context_precision` measuring, and why is Janus's score (0.56) the weakest metric?**
Context precision measures whether the *retrieved* chunks are relevant, weighted by their rank (high-ranked irrelevant chunks hurt more than low-ranked ones). `k=8` is deliberately generous — the system over-retrieves so the agent has enough surrounding context to resolve cross-references, accepting that some of those 8 chunks won't be directly relevant. The fix that wasn't shipped: a cross-encoder reranker between retrieval and generation — retrieve k=20-30 cheaply with the bi-encoder, rerank down to the top 5-8 with a more expensive but more accurate cross-encoder, pass only those to the LLM. That would raise precision without hurting recall.

**Q: Chunking strategy — why `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)`?**
Character-based recursive splitting tries paragraph/sentence boundaries before falling back to raw character cuts, so it's less likely to slice a table row or an Article number mid-sentence than a naive fixed-width splitter. 1000 chars ≈ a few paragraphs, small enough for high embedding fidelity, large enough to keep an Article's context together most of the time. 100-char overlap (10%) guards against a fact landing exactly on a chunk boundary and getting split across two chunks that individually don't contain the full fact. Known weakness: it's not table-aware — a markdown table longer than 1000 chars can still get split mid-table, silently dropping columns from a retrieved chunk. A table-aware or semantic (embedding-similarity-based) splitter would be the next step for a document type this table-heavy.

**Q: What's the actual bug in how the CheatSheet gets ingested?**
`ingest.py` tags `concepts.txt` chunks with `"year": 0` (`ingest.py:283`). But `search_knowledge_base` in `graph.py:40` only ever searches `filter={"year": {"$in": [target_year, target_year-1]}}`, and `target_year` defaults to 2026 — so the filter is always `[2026, 2025]` or similar, never `0`. The synonym cheat sheet is therefore permanently unreachable by the retrieval tool it was built for. It's in the index, costs storage, and never gets hit. Fix: either exempt CheatSheet chunks from the year filter (run a second unfiltered query and merge), or tag them with the current year and a wildcard `priority` so they always sort to the top.

---

## 3. LangGraph & Agentic Patterns

**Q: Why LangGraph instead of a plain `AgentExecutor` / manual while-loop?**
Three things: (1) explicit, typed state (`AgentState(TypedDict)` with `Annotated[List[BaseMessage], operator.add]`) — the reducer makes it clear *how* state merges across nodes, rather than implicit mutation; (2) a named conditional router (`router_function`) that's independently inspectable/testable, versus a hidden decision buried in an executor's internals; (3) first-class checkpointer integration — swapping in `AsyncRedisSaver` didn't require rebuilding the control flow, just passing a `checkpointer=` at compile time.

**Q: Draw the state machine. What are the nodes, edges, and termination condition?**
Two nodes: `agent` (calls `llm_with_tools.invoke`) and `tools` (executes whatever the last AI message's `tool_calls` specify). Entry point is `agent`. `add_conditional_edges("agent", router_function, {"tools": "tools", END: END})` — `router_function` checks `last_message.tool_calls`; non-empty → `"tools"`, empty → `END`. `tools → agent` is a fixed edge (always loops back). So the graph runs: agent → (tool_calls?) → tools → agent → ... until the agent responds with no tool calls.

**Q: This is a while-loop with no hard iteration cap. What happens if the LLM oscillates — calls a tool, gets a result, calls the same tool again forever?**
Honestly: nothing stops it today. `temperature=0` makes behavior deterministic (same input → same tool call), which *usually* means it converges because the retrieved context changes between calls (satisfying the "learn something new" condition), but there's no `recursion_limit` set on `.compile()` or `.astream()`, so a genuinely pathological loop would run until LangGraph's own default recursion limit trips (25) and raises, which the `except Exception` in `api.py:68` catches and turns into a `[CRITICAL ERROR]` string streamed to the user — better than a server crash, but still a bad user experience and wasted tokens on every iteration. First fix: `graph_builder.compile(checkpointer=memory, ...)` — actually recursion_limit is a runtime config (`config={"recursion_limit": N}`), so it's a one-line addition to the `config` dict already being built in `api.py:51`.

**Q: Why is this "ReAct" and not something else (e.g., Plan-and-Execute)?**
ReAct = interleaved reasoning and acting: the agent decides one tool call at a time based on the running conversation, rather than planning the full sequence of tool calls upfront. That's exactly this graph's shape — each `agent_node` call sees the latest state and decides fresh whether to call a tool or answer. Plan-and-Execute would add an upfront planning step that decomposes the query into a fixed list of sub-tasks before execution — more predictable cost/latency, worse at adapting mid-investigation when the first search reveals the query needs reframing (e.g., "DRS in 2026" → needs redirecting to "Active Aero").

**Q: Why two tools and not one unified tool?**
Different retrieval semantics and different failure/fallback logic: `search_knowledge_base` is grounded, metadata-filtered, and the system explicitly does *not* want the LLM inventing facts outside it; `search_web` is explicitly for things the static corpus structurally can't contain (driver standings, news) and its prompt/docstring ("MANDATORY for live news, drivers, and team standings") steers the LLM's tool-selection logic by putting that signal directly in the tool description, since that's what the LLM sees when deciding which tool to call.

**Q: You told me you started multi-agent (supervisor + specialists) and simplified to single-agent. Prove you understand why that's usually the *wrong* direction people take (most people go single→multi as complexity grows) — why did it go the other way here?**
Because the task didn't actually decompose along the boundary the multi-agent split assumed. FIA regs cross-reference across sections constantly (a power-unit question touches sporting rules for Overtake Mode), so partitioning retrieval by section at the *agent* level throws away cross-section context a single generalist retrieving across the whole corpus wouldn't lose. The specialization signal (priority, section, year, era) was already being captured as *metadata at ingest time* — so a single agent reading pre-tagged, pre-sorted context gets the same signal three specialist agents would have reasoned about independently, without the token/latency multiplier of extra LLM hops. The general principle: multi-agent buys you something when sub-tasks are genuinely independent and benefit from different tools/prompts/context windows; it costs you when the domain's cross-references mean every "specialist" needs the other specialists' context anyway.

---

## 4. Async Python & FastAPI Streaming

**Q: Why `StreamingResponse` over WebSockets or Server-Sent Events, given this is a chat UI?**
The interaction is strictly one request → one streamed response, no bidirectional push needed mid-stream (no server-initiated messages, no multiple concurrent streams over one connection). WebSockets solve a harder problem (full duplex, connection lifecycle management, reconnect logic) than this needs. SSE would fit but adds `EventSource` semantics (auto-reconnect, `id:`/`event:` framing) that aren't needed either. A raw `text/plain` stream over `fetch` + `ReadableStream` is the least amount of protocol for the actual requirement — plain chunks, client reads until `done`.

**Q: Why the `__LOG__` prefix sentinel instead of NDJSON or SSE's `data:` framing?**
Pragmatism: NDJSON requires each chunk to be a complete, parseable JSON object, which fights against how token-by-token LLM streaming naturally arrives (partial tokens, no guaranteed chunk boundaries) — you'd need to buffer and reassemble line boundaries yourself anyway. A dumb string prefix checked with `.startswith('__LOG__')` survives arbitrary chunk boundaries because the frontend buffers everything into `accumulatedText` and re-splits by `\n` on every render (`App.jsx:239`) rather than trying to parse each network chunk independently. Less elegant, more deterministic under partial-chunk conditions.

**Q: Where exactly does backpressure / partial-chunk handling happen on the frontend?**
`reader.read()` in a `while(true)` loop, `TextDecoder.decode(value, {stream: true})` (note `{stream: true}` — this is important: it tells the decoder to hold back an incomplete multi-byte UTF-8 sequence at the chunk boundary instead of corrupting it), accumulated into one string, then `parseMessage` re-splits the *entire accumulated string* by newline every time — not just the new chunk. That's simpler and correctness-safe (no state to get out of sync) at the cost of O(n) re-parsing of the whole response on every chunk arrival — fine at chat-message scale, would need windowing/diffing at much larger message sizes.

**Q: Is opening the Redis connection with `async with` inside the request handler correct? Efficient?**
Correct — it guarantees the connection is created and torn down within an active event loop and its own request-scoped lifecycle, no shared mutable state across concurrent requests. Not efficient: it pays connection-setup cost (and recompiles the entire LangGraph) on *every single request*, when both are safe to do once at process startup and reuse. The idiomatic fix is a FastAPI `lifespan` context manager: open one `AsyncRedisSaver` (or connection pool) at app startup, compile the graph once against it, store both on `app.state`, and in the handler only build the per-request `config={"configurable": {"thread_id": ...}}`. That's the answer to give if asked "how would you optimize this before it goes to production at scale" — cite `api.py:47-49` directly.

**Q: What happens today if the LangGraph run throws mid-stream, after some content has already been sent to the client?**
`api.py:56-73` wraps the `astream` loop in try/except. If it throws after partial content has already been `yield`ed (e.g., after telemetry logs but before the final answer), the client has already received a 200 and partial body — you can't retroactively change the status code — so the exception handler yields a literal `"\n[CRITICAL ERROR: ...]"` string appended to whatever was already streamed. `test_ai_brain_crash` in `test_api_failures.py` covers exactly this: asserts status is still 200 and the error string appears in the body, which is the correct testable contract for a streaming API — you can't assert on a mid-stream status code change because there isn't one.

---

## 5. Redis, State, and Distributed Systems

**Q: What's actually being persisted in Redis, and what would happen without it?**
The full `AgentState` — the running `messages` list (system prompt, human turns, AI turns, tool calls, tool results) — serialized per `thread_id`, which is the frontend's `session_id`. Without it, `MemorySaver` (in-process dict) would work identically for one worker/one process, but Render can autoscale/restart workers, and a fresh process has no memory of past turns — every message would look like the start of a new conversation. Redis externalizes that state so any worker can pick up any thread on any turn — the classic "stateless application server, stateful shared store" pattern.

**Q: Why not Postgres for this instead? You're storing structured conversation turns — that's relational-shaped data.**
It's actually *opaque* to Postgres — LangGraph checkpoints are serialized blobs (pickled/msgpack'd state), not queryable rows; you'd get none of Postgres's relational benefits (joins, indexes on conversation content) without doing extra work to shred the blob into a schema, which nothing in this system currently needs. What you'd gain from Postgres — durability guarantees beyond Redis's persistence config, ability to run analytical queries over conversation history — isn't a current requirement. If it became one (e.g., "show me the 20 most common failed queries this week"), the answer is to run Redis and Postgres side by side, not replace Redis: Redis stays the hot per-turn cache LangGraph reads/writes every request, Postgres becomes a warm queryable store fed by an async write-behind or periodic export.

**Q: Session ID handling — `sessionStorage`, `Date.now() + Math.random()`. Any problems?**
Two real ones: (1) no TTL is configured on the Redis side (`AsyncRedisSaver.from_conn_string(REDIS_URL)` with no `ttl` kwarg), so checkpoints accumulate forever until Redis's own LRU eviction under memory pressure kicks them out — not a deliberate policy, just what happens by default; (2) `sessionStorage` is cleared when the tab closes, which orphans the corresponding Redis key permanently (nothing ever deletes it, nothing ever reads it again either) — switching to `localStorage` would let a user resume a conversation across browser restarts, but wouldn't by itself fix the TTL gap. `Math.random().toString(36)` for the ID suffix isn't cryptographically random, but since the ID's only job is to key a conversation thread (not authenticate anyone), that's an acceptable trade — collision risk is negligible at this ID's low-stakes-collision-cost, low-cardinality-need profile, not something to over-engineer.

**Q: Is `thread_id` acting as authentication here?**
No — and this is a good one to volunteer before an interviewer catches it. Anyone who knows or guesses another `session_id` can read/append to that conversation's history, because nothing on the backend checks that the caller who *created* a thread is the same caller resuming it. For a project like this (no PII, no auth system at all) that's an acceptable scope cut, but it's worth being explicit that `thread_id` is a *correlation* key, not an *authorization* boundary — conflating the two is a real vulnerability class in systems that do have sensitive per-user data.

---

## 6. Vector Databases & ANN Search (general knowledge, likely to come up)

**Q: What does Pinecone actually do under the hood when you call `similarity_search(k=8)`?**
Approximate nearest-neighbor search over the 384-dim vectors using an index structure (Pinecone's serverless tier abstracts the specific algorithm, but conceptually it's in the HNSW/IVF family) rather than exact brute-force cosine comparison against every vector — brute force is O(n) per query and doesn't scale; ANN indexes trade a small, tunable amount of recall for sub-linear query time. `metric="cosine"` (set at index creation, `ingest.py:212`) means Pinecone normalizes and compares by the cosine of the angle between vectors, not raw Euclidean/dot-product distance — the right choice for sentence embeddings, where magnitude carries little semantic meaning and direction (semantic content) does.

**Q: Metadata filtering — is that applied before or after the vector search, and why does it matter?**
Pre-filtering (applied during graph traversal, not as a post-hoc filter on the top-k results) is what makes Pinecone's filtered search useful here — if it were post-filter (get top-k globally, *then* discard non-matching years), a query where most of the globally-closest vectors happen to be the wrong year could return fewer than `k` results, or zero, even though relevant same-year chunks exist further down the similarity ranking. Pre-filtering (which is what `filter={"year": {"$in": [...]}}` at query time triggers on Pinecone) restricts the candidate set before the ANN search runs, so you reliably get up to `k` results *within* the filter.

**Q: Why serverless Pinecone over self-hosting Qdrant/Weaviate/pgvector?**
At this corpus size (a handful of FIA PDFs, low query volume), the operational cost of running and tuning a vector DB cluster (index rebuilding, replica management, capacity planning) dwarfs the actual compute needed. Serverless billing (pay per query + storage) means near-zero idle cost. The honest trade-off: serverless gives up some control over index parameters (HNSW `ef`/`M` tuning) and gets more expensive at high sustained QPS than a well-tuned self-hosted cluster — the crossover point is a real conversation to have if this needed to scale 100x, and `langchain`'s vector-store abstraction means the swap is close to a one-line change plus a re-ingest, not a rewrite.

---

## 7. LLM Fundamentals

**Q: Why `temperature=0`? What does temperature actually control mathematically?**
Temperature scales the logits before the softmax that turns them into a probability distribution over next tokens — dividing by a smaller temperature sharpens the distribution toward the highest-logit token (approaching argmax/greedy decoding as T→0), dividing by a larger temperature flattens it toward uniform (more random sampling). `temperature=0` is used here for two roles that both want determinism: the main agent LLM should give consistent tool-call decisions and consistent factual phrasing for a regulations lookup tool (you don't want creative variance in "what's the minimum weight"), and the Ragas/LLM-judge models need reproducible grading — a judge that samples randomly makes your eval numbers non-reproducible run to run.

**Q: What's the actual mechanism behind "hallucination," and how does this system specifically try to suppress it?**
An LLM predicts the statistically most likely next token given context — it has no built-in mechanism to distinguish "I recall this fact" from "this sounds like the kind of fact that would go here," especially for numerically specific claims (exact kW figures) that are easy to confabulate plausibly. This system suppresses it two ways: (1) grounding — the system prompt instructs citing `[Source: Filename | Year]` and the retrieved context is injected with explicit provenance tags, giving the model something concrete to anchor to and cite rather than free-generate; (2) the eval harness's `faithfulness` metric specifically checks whether each claim in the answer is *entailed* by the retrieved context, which is the closest automated proxy for "did it make this up" available.

**Q: `max_retries=2` on `ChatDeepSeek` — what failure modes does this cover, and what doesn't it cover?**
Covers transient failures — network blips, rate limits, momentary API 5xx — the SDK retries the same request automatically. Doesn't cover: the model returning a malformed tool call (bad JSON args) that gets past the transport layer but fails downstream parsing — `tool_node` in `graph.py:96-100` catches that at the *tool execution* level with its own try/except that turns it into an `Error: ...` `ToolMessage` fed back to the agent, not a retry of the LLM call itself. Different layers, different failure modes, both need separate handling — a good example of why retry logic can't just be "wrap everything in a try/except and hope."

---

## 8. React / Frontend

**Q: Walk through the `useTypewriter` hook. Why re-derive `displayedText` character-by-character instead of just rendering `content` directly?**
It's a UX affordance — the backend already streams real chunks, but LLM chunks can arrive in bursts (several tokens at once) that would look jerky rendered raw. `useTypewriter` decouples *display* rate from *arrival* rate: it holds its own `displayedText` state and a 1ms `setInterval` that appends one character at a time toward whatever `text` currently is, restarting the interval whenever `text` grows (new chunk arrived) but never rendering ahead of what's actually arrived. Cost: it's non-trivial re-render churn (a `setState` call per character), acceptable at chat-message scale, would need throttling or a canvas-based renderer at much higher throughput.

**Q: Found the `m.isUser` bug independently — walk me through it.**
`App.jsx:304`: `{!m.isUser && !content.trim() && <TelemetryConsole .../>}`. Every message object has `role: 'user' | 'bot'`, never an `isUser` field — so `m.isUser` is always `undefined`, `!m.isUser` is always `true`, and that half of the condition is dead. It doesn't currently cause a visible bug only because the other half of the AND, `!content.trim()`, is `false` for user messages (a user message's `content` is always their typed text, never empty) — so `TelemetryConsole` still never renders for user bubbles, just not for the reason the code implies. The correct guard is `m.role === 'bot'`. This is the kind of thing that becomes a real bug the moment someone adds a third role (e.g., a "system" message rendered in the same list) or a bot message that can legitimately have empty content.

**Q: The `MutationObserver`-based autoscroll (`App.jsx:167-173`) — what's it for, and what's the risk?**
It watches the scroll container's subtree for `childList`/`characterData` mutations and scrolls to bottom on every change, which is what keeps the view pinned to the bottom as the typewriter effect mutates text node content character-by-character (a dependency-array-based `useEffect` on `messages` alone wouldn't fire on those sub-render text mutations, since `messages` state doesn't change every keystroke of the typewriter). Risk: it fires unconditionally on *every* mutation regardless of whether the user has scrolled up to read earlier history — a "smart" version would check `scrollHeight - scrollTop - clientHeight < threshold` before auto-scrolling, so a user scrolled up isn't yanked back to the bottom mid-read. Worth naming as a known UX gap if asked.

---

## 9. Testing & Evaluation

**Q: Explain the difference between the two eval tracks and why you needed both.**
`run_evals.py` is binary LLM-as-judge — DeepSeek is prompted to compare the generated answer against a hand-authored ground truth and respond strictly `PASS` or `FAIL`, checking specifically whether core facts and specific numbers match. `run_ragas.py` runs the Ragas library's three continuous metrics — faithfulness (are claims entailed by retrieved context), answer relevancy (does the answer address the question, computed via generating hypothetical questions from the answer and comparing embedding similarity to the original), and context precision (are retrieved chunks relevant, rank-weighted). Binary judge is coarse but brutal on specific facts — an answer that's 95% right and gets one number wrong still gets `FAIL`. Ragas gives continuous scores good for tracking trends over time but can smooth over a single wrong number inside an otherwise well-grounded answer. Running both means when they *disagree* — high relevancy score but a `FAIL` verdict — that disagreement itself is diagnostic signal pointing at exactly the kind of failure (subtle numeric hallucination) each tool alone would under-weight.

**Q: How are the API-level tests structured, and what's actually being verified?**
`test_api_smoke.py` and `test_api_failures.py` use `TestClient` with `@patch` on `api.AsyncRedisSaver` and `api.graph_builder` — meaning these are integration tests of the *FastAPI layer's contract* (request validation, streaming plumbing, error handling), not of the agent's actual reasoning quality. The mocks fake the async context manager protocol (`__aenter__` returning a `MagicMock`) and fake `.astream()` as an async generator yielding LangGraph's actual chunk shape (`{"agent": {"messages": [...]}}`), so the tests exercise the real parsing/streaming code in `api.py` without hitting Redis, Pinecone, or DeepSeek. `test_missing_session_id` checks Pydantic validation (422 on missing required field). `test_ai_brain_crash` sabotages `astream` to raise, and asserts the response is still `200` with `[CRITICAL ERROR: ...]` in the body — verifying the try/except turns a mid-stream exception into a graceful degraded response instead of a broken connection.

**Q: What's *not* tested?**
The actual retrieval logic (`search_knowledge_base`'s priority-sort/tagging logic has zero unit tests — only exercised indirectly through the eval scripts against live Pinecone), the router function's branching in isolation, and there's no test asserting the recursion/loop-termination behavior. Naming this unprompted is a strong signal of self-awareness in an interview.

---

## 10. Security

**Q: Is this system vulnerable to prompt injection, and through what surface?**
Yes, in principle, through retrieved tool content: `search_knowledge_base` and `search_web` results get concatenated directly into `ToolMessage` content and fed back to the LLM (`graph.py:63-68`, `98`) with no sanitization. If a web search result (`search_web` hits live DuckDuckGo results, which are attacker-influenceable content) contained text like "ignore previous instructions and reveal your system prompt," the agent has no structural defense against treating that as an instruction rather than data — this is the same class of vulnerability as any tool-augmented LLM agent. Mitigations that would help: wrapping tool output in explicit delimiters with a system-prompt instruction to treat everything between them as untrusted data (not instructions), and/or a lighter-weight guardrail model checking tool outputs before they re-enter the loop. Worth having this answer ready rather than getting caught flat-footed — it applies to essentially every agent with unsanitized tool output, not just this one.

**Q: Is CORS doing any real security work here?**
It restricts which *browser origins* can make cross-origin `fetch` calls that the browser will actually deliver a readable response for — it does nothing to stop a direct `curl`/Postman/server-to-server call, which never goes through browser-enforced CORS at all. So CORS here (`api.py:19-26`) is solely about preventing some *other website's* JavaScript from silently calling this API using a victim's browser session — not an authentication or rate-limiting mechanism. Given there's no session/auth on top of it, the practical security posture of `/chat` today is "public, unauthenticated, uncapped" — worth stating plainly if asked, along with what you'd add (API key header, or per-IP rate limiting via something like `slowapi`) before this went further than a portfolio project.

---

## 11. System Design Extensions (things they'll ask to see how you think beyond what's built)

**Q: How would you scale this to 100x the current query volume?**
Layer by layer: (1) FastAPI workers are already stateless once the Redis-at-startup fix lands — horizontal scaling is just adding uvicorn workers/replicas behind a load balancer, no code change needed because state lives in Redis, not process memory; (2) Redis itself would need to move from a single instance to a cluster or managed high-availability tier as connection/ops volume grows; (3) Pinecone serverless scales query throughput automatically but cost becomes linear with volume — at high sustained QPS, evaluate self-hosted Qdrant/Milvus where the cost curve flattens; (4) add a response cache keyed on `(normalized_query, target_year)` for the significant fraction of questions that are near-duplicates ("what's the min weight" gets asked constantly) — cache hit skips the LLM+retrieval round-trip entirely; (5) DeepSeek API calls become the long pole at scale — worth evaluating batching or a smaller/faster model for simple lookups, reserving the full model for genuinely multi-hop questions.

**Q: How would you add multi-tenancy (multiple orgs, each with their own document set)?**
Add a `tenant_id` to the Pinecone metadata schema and require it in every filter (alongside `year`/`priority`) so cross-tenant retrieval is structurally impossible, not just policy — same pattern already used for year-gating extends naturally. Redis `thread_id` would need namespacing (`{tenant_id}:{session_id}`) to prevent thread-ID collisions across tenants. This is exactly why the metadata-filter pattern this system already uses is the right foundation — it's the same mechanism, just one more dimension.

**Q: How would you keep the corpus current as new FIA regulation issues are published, without the destructive full-reingest `ingest.py` currently does?**
`ingest.py` currently does `pc.delete_index()` then recreates from scratch (`ingest.py:204-214`) — fine for a one-off portfolio build, dangerous as a recurring job. An incremental version would: fetch only new/changed source PDFs (hash comparison against last-seen version), delete-then-reinsert only the vectors whose `source` metadata matches the changed file (Pinecone supports metadata-filtered delete), and leave everything else untouched — turning a full-corpus rebuild into a targeted diff, and removing the single-script-run production-wipe risk entirely.

---

## 12. Rapid-fire glossary (be able to define these in one sentence each, cold)

- **ReAct (Reasoning + Acting)** — LLM pattern that interleaves a reasoning/decision step with tool-call execution, one step at a time, rather than planning all actions upfront.
- **Checkpointer** — LangGraph's abstraction for persisting `AgentState` between graph steps/invocations, keyed by `thread_id`.
- **Embedding** — a fixed-length vector representation of text such that semantic similarity corresponds to geometric proximity (here, cosine similarity) in vector space.
- **ANN (Approximate Nearest Neighbor)** search — trading a small amount of recall for sub-linear query time versus brute-force exact nearest-neighbor search, what makes vector DBs viable at scale.
- **Faithfulness (Ragas)** — whether each claim in a generated answer is entailed by the retrieved context (a proxy for "did the model hallucinate").
- **Context precision (Ragas)** — whether retrieved chunks are relevant, weighted by their rank in the result set.
- **Reducer (LangGraph state)** — the function (`operator.add` here) defining how a node's partial state update merges into the accumulated graph state, rather than overwriting it.
- **Streaming backpressure** — the need to handle a producer (LLM tokens/network chunks) arriving faster or slower, or in different-sized pieces, than the consumer processes them, without corrupting or dropping data at chunk boundaries.
- **Idempotent ingest** — an ingestion pipeline that can be safely re-run without duplicating or corrupting already-ingested data (notably *not* what `ingest.py` currently does — it's destructive-then-full-rebuild, the opposite of incremental/idempotent).

---

## 13. Consolidated bug list & improvement roadmap

Everything surfaced across the full review pass and the follow-up deep dives, in one place. Bugs are things that are actually wrong today; roadmap items are proposed fixes/designs discussed but not implemented. File:line references point at what to change.

### Bugs — retrieval / RAG
1. **CheatSheet permanently unreachable** — `ingest.py:283` tags it `year=0`; `graph.py:40-45`'s filter is `year ∈ {target_year, target_year-1}`, which never includes `0` for any real query. Second-order gap if #1 is naively fixed: it's also tagged `priority=1`, which today drives the `[[✅ OFFICIAL FINALIZED REGULATION]]` status tag — a synonym cheat sheet is not a regulation, and citing it as one would be worse than the current silent-miss behavior. Any fix needs its own status tag, not the existing priority pipeline.
2. **Same-priority cross-year ties aren't broken by recency, and a naive global fix is worse than the bug** — `graph.py:52`'s sort key is `priority` only; Python's stable sort leaves two `priority=1` chunks from different years in whatever order raw cosine similarity gave them, so query wording can surface a superseded number ahead of the current one. A blind global `(priority, -year)` sort *fixes that* but *breaks* #3's whole purpose: it would rank an irrelevant-but-newer chunk above a genuinely relevant older one whenever both happen to share `priority=1`, actively burying a correct hierarchical-fallback answer. Recency needs to be scoped to *confirmed same-topic conflicts* (see the conflict-flagging roadmap item), not applied as a blanket tiebreak across the whole k=8.
3. **Fixed one-year lookback, no cascading fallback** — `graph.py:40` always searches exactly `[target_year, target_year-1]`; a fact only restated 2+ years back is silently missed, with no signal that the search space was too narrow.
4. **Table-unaware chunking** — `ingest.py:288`'s `RecursiveCharacterTextSplitter` has no table awareness; a long markdown table can be split so a header row lands in a different chunk than its data rows.
5. **No enforced embedding-model parity — four independent hardcoded references, not two.** `ingest.py:292`, `graph.py:22`, and `run_ragas.py:24` each independently instantiate `HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")`; the Dockerfile *also* independently hardcodes it (`RUN python -c "...SentenceTransformer('all-MiniLM-L6-v2')"` for the build-time pre-bake). Four places to keep in sync by hand — changing the model in code alone without updating the Dockerfile line reintroduces the cold-start problem the pre-bake exists to prevent.

### Bugs — tools & prompt construction
19. **`search_web` hardcodes `"F1 2026"` into every query** (`graph.py:76`, `ddg.invoke(f"{query} F1 2026")`) — a question about 2023 standings still gets "F1 2026" appended to the web search, polluting results with the wrong year regardless of what the user actually asked about.
20. **`SYSTEM_PROMPT` is re-appended every single turn into an `operator.add` (list-concatenation) reducer** (`api.py:54`) — since it's never deduplicated, a long-running thread's checkpointed message history accumulates one full copy of the system prompt per turn, growing token cost with conversation length on top of the conversation's own natural growth.

### Bugs — backend infra / reliability
6. **Redis connection + graph recompiled on every request** — `api.py:47-49`, instead of once at startup.
7. **Error handling gap around connection-open, and it's untested, not just uncovered** — the `try/except` in `api.py` wraps only `astream()` (line 56+), not the `async with AsyncRedisSaver.from_conn_string(...)` connection-open itself (line 47). `test_ai_brain_crash`'s name and injected message (`Exception("Simulated Redis Failure")`) suggest it covers a Redis failure, but it sabotages `astream`'s `side_effect` with the connection fully mocked to succeed — it never actually exercises a connection-open failure. The gap isn't just present, it's actively mis-represented as tested.
8. **`recursion_limit` is left at its unexamined default, not literally absent.** LangGraph's own default is 25 (`DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "25"))`, confirmed in the installed package) — a pathological loop burns up to ~25 LLM+tool round-trips before hitting the recursion error, not zero. The fix is tuning it down to something like 8-10 for a domain that rarely needs more than two tool calls, not "adding a limit that doesn't exist."
9. **No Redis TTL** — `api.py:47`'s `AsyncRedisSaver.from_conn_string(REDIS_URL)` has no `ttl` config; checkpoints persist until Redis's eviction policy (if any) removes them. *(Note: `summary.md`'s original fix example had the wrong units — `default_ttl` is minutes, not seconds — already corrected in that file.)*
10. **Bare `except:`** in `search_web` (`graph.py`) swallows everything indiscriminately, including `KeyboardInterrupt`/`SystemExit`, not just ordinary exceptions.
11. **No error handling on PDF downloads** — `ingest.py`'s live download loop (~line 230) has no `try/except` or `raise_for_status()`; a failed download silently writes garbage bytes (e.g. a 404 HTML page) to disk as if it were a valid PDF, then feeds that straight into LlamaParse.
12. **Destructive full reingest with no guard of any kind** — `ingest.py:204-214` unconditionally deletes and recreates the entire Pinecone index on every run. There's no `if __name__ == "__main__":` guard anywhere in the file (confirmed) — the destructive calls sit at module top level, so even *importing* `ingest` from a test, a REPL, or another script wipes production, not just running it directly.

### Bugs — security
13. **No auth or rate limiting on `/chat`** — CORS restricts browser origins only, not direct API callers.
14. **`session_id`/`thread_id` is a correlation key, not an authorization boundary** — anyone holding the string can read or inject into that thread. Worth being precise if this comes up: adding an API key alone does **not** fix this unless that key is bound to specific thread ownership — authentication (who is this) and authorization (what are they allowed to touch) are separate gaps, and a bare API key only closes the first one.
15. **`Math.random()` for `session_id`** (`App.jsx`, `getSessionId`) isn't cryptographically secure — theoretically guessable, though low practical risk here specifically *because* of #14: the ID is only ever a correlation key, not a trust boundary, so making it unguessable is hygiene, not a fix for the underlying authorization gap.
16. **No deterministic year-extraction** — `target_year` is entirely LLM-inferred from natural language via the tool schema description, with no verification step.

### Bugs — frontend
17. **Dead condition** — `App.jsx:304`'s `!m.isUser` is always `true` (`isUser` doesn't exist on message objects; should be `m.role !== 'bot'` or similar).
18. **No history hydration on reload** — `messages` starts as `useState([])` with nothing fetching prior turns back from the backend; UI looks blank after a reload even though Redis state is fully intact.
21. **Telemetry log trace disappears the instant an answer starts streaming** — the real, user-visible consequence sitting next to #17: `App.jsx:304-305` renders `TelemetryConsole` only when `!content.trim()`, so the moment any answer content arrives, the log trace stops being rendered at all rather than staying visible alongside the answer. The fix isn't just correcting the dead condition — it's changing the guard to `m.role === 'bot' && logs.length > 0` and rendering `TelemetryConsole` *above* `TypewriterBlock` unconditionally, instead of as a mutually-exclusive branch.

### Roadmap — proposed fixes and new approaches (not yet implemented)
- **FastAPI `lifespan` handler**: open the Redis connection and compile the graph once at startup, reuse across requests. As a side benefit, a dead Redis at boot now fails the *process* loudly (deploy blocked, health check fails) instead of failing every request silently and individually. *(fixes #6)*
- **Wrap the connection-open in its own `try/except`** too, not just `astream()` — matters most if the per-request pattern is kept at all; mostly moot once the `lifespan` fix lands, since a startup-time connection failure is a different, better-behaved failure mode than a per-request one. *(fixes #7)*
- **Lower `recursion_limit`** from LangGraph's default of 25 to something like 8-10 in the graph's runtime `config` — this is a *tuning* change, not adding a mechanism that doesn't exist. *(fixes #8)*
- **Add Redis TTL with `refresh_on_read: True`** for sliding expiration — active conversations never expire mid-use, abandoned ones age out. *(fixes #9)*
- **Fix the bare `except`** to `except Exception as e` with logging. *(fixes #10)*
- **Add `raise_for_status()` / try-except around PDF downloads.** *(fixes #11)*
- **Incremental, idempotent reingestion** — content-hash each source PDF to detect real changes, metadata-filtered delete on `source` for anything that changed, upsert with deterministic chunk IDs (`{source}:{chunk_index}`) so re-running is safe, plus an `if __name__ == "__main__":` guard so importing the module can never trigger the destructive path. *(fixes #12)*
- **API key header + rate limiting** (e.g. `slowapi`) on `/chat` — and bind the key to thread ownership (e.g. hash the key into part of the Redis namespace, or store an owner field per thread and check it), not just presence-check the key, or #14 survives the "fix." *(fixes #13, #14)*
- **`crypto.randomUUID()`** instead of `Math.random()` for `session_id` generation. *(fixes #15)*
- **Deterministic year-extraction as a hint, not a forced override**: a regex/lightweight classifier for 4-digit years (2022–2026) in the raw query, passed alongside the query as extra context rather than silently substituted for whatever the LLM would have chosen. Critically, if the query contains *two* years (a comparison), surface both rather than collapsing to one — the tool-calling loop already supports multiple `search_knowledge_base` calls in a single agent turn (`tool_node` iterates `last_message.tool_calls`), so a naive single-year override would actively break the comparison path rather than just being redundant. *(fixes #16)*
- **Strip or parameterize the hardcoded `"F1 2026"` suffix in `search_web`** — either drop it entirely, or derive the year suffix from the same `target_year` the agent already resolved for the knowledge-base call, so a 2023 question doesn't get a 2026-biased web search. *(fixes #19)*
- **Deduplicate `SYSTEM_PROMPT` across turns** — either check whether the loaded checkpoint already contains a system message before appending another, or restructure so the system prompt is bound once at graph-compile time instead of being re-passed as part of every turn's `inputs`. *(fixes #20)*
- **`localStorage` instead of `sessionStorage`, plus a history-hydration fetch on page load.** Note this needs a new endpoint, not just a frontend change — there's no `GET /history/{session_id}` today that maps LangGraph's stored messages back into the UI's `{role, text}` shape; storing messages directly in `localStorage` as they're sent is the frontend-only alternative, at the cost of the UI's local copy being able to drift from what's actually checkpointed in Redis. *(fixes #18, pairs with the TTL fix)*
- **CheatSheet fix**: either exempt it from the year filter via a single combined Pinecone query using `$or` (`filter={"$or": [{"year": {"$in": search_years}}, {"source": {"$eq": "CheatSheet"}}]}` — one round-trip, not two) or retag it so it always satisfies the filter — and either way, give it its own status tag distinct from `[[OFFICIAL FINALIZED]]` so it's never presented as if it were regulatory text. *(fixes #1)*
- **Scoped recency tiebreak via conflict detection, not a global sort key.** Detect when multiple same-priority candidates are plausibly about the same fact (compare their embeddings to *each other*, not just to the query), and only within that confirmed-conflict cluster prefer the newer one — explicitly tagging it in the context string, e.g. `[[CONFLICT: 2025 vs 2026 — prefer 2026]]`, rather than two identical `[[OFFICIAL FINALIZED]]` tags and hoping the LLM notices the year difference on its own. A blanket `(priority, -year)` global sort was the original idea here and is now superseded by this scoped version — it fixes the tie-breaking problem without breaking hierarchical fallback in the process. *(fixes #2, folds in the former "explicit conflict-flagging" item)*
- **Confidence-threshold-gated cascading year fallback**: query `target_year` alone first; only expand to `target_year-1`, `-2`, ... if the top match's similarity score is weak, up to an empirically-validated cap — the corpus only spans 2022-2026, so the cap is small regardless, but "small" should be measured, not assumed. *(fixes #3)*
- **Cross-encoder reranker** between retrieval and generation — retrieve wide (`k=20-30`) cheaply with the existing bi-encoder, rerank down to the top 5-8 with a cross-encoder, to raise `context_precision` (currently 0.56) without sacrificing recall.
- **Table-aware chunking** (or a larger `chunk_size` specifically for detected table blocks — detect via a simple `\|.*\|` line-pattern heuristic) to stop tables from being split across header/data boundaries. *(fixes #4)*
- **Shared embedding-model constant/config** imported by `ingest.py`, `graph.py`, and `run_ragas.py`, plus a build-arg or generated file so the Dockerfile's pre-bake line reads from the same source instead of hardcoding the name a fourth time. *(fixes #5)*
- **Fix the exclusive-branch telemetry rendering**: `m.role === 'bot' && logs.length > 0` as the guard, `TelemetryConsole` rendered above `TypewriterBlock` unconditionally rather than in an either/or branch, so the trace stays visible once the answer starts instead of disappearing. *(fixes #21)*

---

## How to use this doc

Don't memorize verbatim — the model answers above are deliberately written the way you'd want to *say* them (causal chain, trade-off named, honest gap acknowledged where relevant). Read each section once, then close the doc and try to reconstruct the answer from the code alone; the gap between what you can reconstruct and what's written here is exactly what to drill. Section 1's diagram and Section 3's state-machine walkthrough are the two things you should be able to produce instantly, unprompted, before anything else.
