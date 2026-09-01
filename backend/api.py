import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from graph import graph_builder, warm_dependencies
from config import (
    DEFAULT_YEAR,
    RATE_LIMIT_PER_MINUTE,
    RECURSION_LIMIT,
    REDIS_TTL_MINUTES,
    env_flag,
)
from year_extract import extract_years, year_hint

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("janus.api")

YEAR_HINT_RE = re.compile(r"\n\n\[YEAR HINT:.*?\]", re.DOTALL)
_rate_hits = defaultdict(list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open Redis (or in-memory fallback) and compile the graph once at startup."""
    app.state.graph = None

    if env_flag("JANUS_SKIP_REDIS"):
        logger.warning("JANUS_SKIP_REDIS set — using in-memory checkpointer, skipping model warm-up")
        app.state.graph = graph_builder.compile(checkpointer=MemorySaver())
        yield
        return

    await asyncio.to_thread(warm_dependencies)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    ttl = {"default_ttl": REDIS_TTL_MINUTES, "refresh_on_read": True}
    try:
        try:
            saver_cm = AsyncRedisSaver.from_conn_string(redis_url, ttl=ttl)
        except TypeError:
            logger.warning("This Redis saver build does not accept ttl=; connecting without TTL")
            saver_cm = AsyncRedisSaver.from_conn_string(redis_url)
        async with saver_cm as memory:
            app.state.graph = graph_builder.compile(checkpointer=memory)
            logger.info("Redis checkpointer ready (TTL %s min, sliding)", REDIS_TTL_MINUTES)
            yield
    except Exception:
        logger.exception("Redis connection failed at startup")
        raise


app = FastAPI(title="JANUS F1 MISSION CONTROL", version="2.0.0", lifespan=lifespan)

origins = ["http://localhost:5173", "http://127.0.0.1:5173", os.getenv("FRONTEND_URL", "")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in origins if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str


SYSTEM_PROMPT = SystemMessage(content="""
    You are **Janus 2.0**, the F1 Technical Director.
    1. DEFAULT TO 2026: Prioritize new regs unless a YEAR HINT or the user names another year.
    2. STRICT ISOLATION: Do not mix years unless comparing.
    3. VISUALS: Use Markdown tables.
    4. CITE: Use [Source: Filename | Year: 20XX].
    5. If a YEAR HINT is present, pass those years as target_year (call the tool once per year when comparing).
    6. TOOL BUDGET: search_knowledge_base already returns your top reranked chunks for that
       year in one call — treat each result as comprehensive, not a preview. Call it AT MOST
       ONCE per year per question (so at most twice for a two-year comparison). Never call it
       again for a year you already searched just to look for "more specific" details — if the
       first search under-delivers, answer with what you retrieved and say plainly what the
       regulations don't specify, rather than searching again.
""")


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    expected = os.getenv("JANUS_API_KEY")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = 60.0
    _rate_hits[ip] = [t for t in _rate_hits[ip] if now - t < window]
    if len(_rate_hits[ip]) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_hits[ip].append(now)


def thread_config(session_id: str) -> dict:
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def _strip_year_hint(text: str) -> str:
    return YEAR_HINT_RE.sub("", text or "").strip()


def serialize_history(messages) -> list:
    """Map LangGraph messages to the frontend {role, text, id} shape."""
    ui = []
    pending_logs = []
    for msg in messages or []:
        msg_type = getattr(msg, "type", "") or ""
        if msg_type == "system":
            continue
        if msg_type == "human":
            ui.append({
                "role": "user",
                "text": _strip_year_hint(msg.content),
                "id": f"hist-user-{len(ui)}",
                "isStreaming": False,
            })
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                for tc in tool_calls:
                    args = tc.get("args") if isinstance(tc, dict) else {}
                    year = (args or {}).get("target_year", DEFAULT_YEAR)
                    pending_logs.append(f"🔍 ANALYZING {year} REGS...")
                pending_logs.append("✅ DATA SECURED.")
            elif msg.content:
                prefix = "".join(f"__LOG__{log}\n" for log in pending_logs)
                ui.append({
                    "role": "bot",
                    "text": prefix + str(msg.content),
                    "id": f"hist-bot-{len(ui)}",
                    "isStreaming": False,
                })
                pending_logs = []
    return ui


@app.get("/health")
async def health():
    return {"status": "ok", "graph_ready": getattr(app.state, "graph", None) is not None}


@app.get("/history/{session_id}", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def history(session_id: str, request: Request):
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialized")
    try:
        snapshot = await graph.aget_state(thread_config(session_id))
    except Exception:
        logger.exception("Failed to load history for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to load history")
    values = snapshot.values if snapshot else {}
    return {"session_id": session_id, "messages": serialize_history(values.get("messages"))}


@app.post("/chat", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def chat_endpoint(request: ChatRequest, raw_request: Request):
    graph = raw_request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialized")

    async def event_generator():
        config = thread_config(request.session_id)
        yield "__LOG__📡 ESTABLISHING UPLINK...\n"

        content = request.message + year_hint(request.message)
        try:
            snapshot = await graph.aget_state(config)
            existing = (snapshot.values or {}).get("messages") if snapshot else None
        except Exception:
            logger.exception("Failed to read checkpoint; treating as new thread")
            existing = None

        if existing:
            inputs = {"messages": [HumanMessage(content=content)]}
        else:
            inputs = {"messages": [SYSTEM_PROMPT, HumanMessage(content=content)]}

        try:
            async for stream_kind, payload in graph.astream(
                inputs, config=config, stream_mode=["updates", "messages"]
            ):
                if stream_kind == "updates":
                    if "agent" in payload:
                        msg = payload["agent"]["messages"][-1]
                        if msg.tool_calls:
                            for t in msg.tool_calls:
                                years = extract_years(request.message)
                                year = t["args"].get("target_year", years[0] if years else DEFAULT_YEAR)
                                # Leading \n: the model can stream preamble content (via the
                                # "messages" branch below) immediately before deciding to call
                                # a tool, with no guaranteed trailing newline. Without this, the
                                # frontend's line-based __LOG__ parser sees one glued line and
                                # renders the raw marker as visible text.
                                yield f"\n__LOG__🔍 ANALYZING {year} REGS...\n"
                    elif "tools" in payload:
                        yield "\n__LOG__✅ DATA SECURED.\n"
                elif stream_kind == "messages":
                    message_chunk, metadata = payload
                    if metadata.get("langgraph_node") == "agent" and message_chunk.content:
                        yield message_chunk.content
        except Exception:
            logger.exception("Graph run failed")
            yield "\n[CRITICAL ERROR: something went wrong processing that request. Please try again.]"

    return StreamingResponse(event_generator(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
