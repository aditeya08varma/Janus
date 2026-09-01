import asyncio
import logging
import operator
import os
import threading
from typing import Annotated, TypedDict, List

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langchain_community.tools import DuckDuckGoSearchRun
from pydantic import BaseModel, Field

from config import DEFAULT_YEAR, EMBEDDING_MODEL, INDEX_NAME, ML_INIT_LOCK, RETRIEVAL_K, load_offline_first
from retrieval import format_context, rerank_docs, retrieve_with_cascade, sort_results
from year_extract import resolve_search_years

load_dotenv()
logger = logging.getLogger(__name__)

if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

_vectorstore = None
_ddg = None
_ddg_lock = threading.Lock()


def get_vectorstore():
    """Thread-safe lazy singleton.

    Tool calls now run concurrently (see tool_node), so two threads can reach
    this on first use at the same time. Guarded by the shared ML_INIT_LOCK
    (see config.py) rather than a private lock: concurrent first-touch of
    PyTorch/tokenizers' native init from two threads has been observed to
    segfault the process even when the two threads are constructing
    *different* models (this vs. retrieval.get_reranker()) on separate locks.
    warm_dependencies() populates this before any request arrives in
    production, so the lock is a dev/JANUS_SKIP_REDIS-path safety net, not
    the primary defense.
    """
    global _vectorstore
    if _vectorstore is None:
        with ML_INIT_LOCK:
            if _vectorstore is None:
                import torch
                from langchain_pinecone import PineconeVectorStore
                from langchain_huggingface import HuggingFaceEmbeddings

                # Tool calls now run concurrently (multiple Python threads via
                # asyncio.to_thread). Left at its default, PyTorch also spins up
                # its own multi-threaded op pool *inside* each thread's forward
                # pass — two or more of those pools fighting over the same few
                # CPU cores causes severe oversubscription (observed: a single
                # 2-year comparison query stalling for minutes instead of
                # seconds). Pinning to 1 makes each embedding call single-
                # threaded so the outer, thread-level concurrency is the only
                # parallelism in play.
                torch.set_num_threads(1)
                # device="cpu": left unset, sentence-transformers auto-detects and
                # silently picks MPS on Apple Silicon. PyTorch's MPS backend isn't
                # safe for concurrent access from multiple threads (root cause of
                # crashes reproduced under concurrent tool calls) — and production
                # (Render, no GPU) only ever uses CPU anyway, so autodetection here
                # was exercising a code path prod never runs.
                embeddings = load_offline_first(
                    lambda: HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL, model_kwargs={"device": "cpu"})
                )
                _vectorstore = PineconeVectorStore(index_name=INDEX_NAME, embedding=embeddings)
    return _vectorstore


def get_ddg():
    global _ddg
    if _ddg is None:
        with _ddg_lock:
            if _ddg is None:
                _ddg = DuckDuckGoSearchRun()
    return _ddg


def warm_dependencies():
    """Force-load the embedding model, vectorstore, and reranker once, up front.

    Called from FastAPI's lifespan so the first real user turn does not pay
    for CrossEncoder/SentenceTransformer instantiation on top of its own
    Pinecone + LLM latency.
    """
    from retrieval import get_reranker

    get_vectorstore()
    get_reranker()


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]


class SearchInput(BaseModel):
    query: str = Field(description="The technical term to search for.")
    target_year: int = Field(default=DEFAULT_YEAR, description="Specific year to prioritize.")


@tool("search_knowledge_base", args_schema=SearchInput)
def search_knowledge_base(query: str, target_year: int = DEFAULT_YEAR):
    """Accesses FIA F1 Regulations with batch retrieval, CheatSheet access, and priority-gated fallback."""
    try:
        search_years = resolve_search_years(query, target_year)
        docs, years_used = retrieve_with_cascade(get_vectorstore(), query, search_years)
        if not docs:
            return "No relevant regulations found."
        ranked = rerank_docs(query, sort_results(docs), top_k=RETRIEVAL_K)
        context = format_context(ranked)
        return f"[search_years={years_used}]\n{context}"
    except Exception as e:
        logger.exception("Vector search failed")
        return f"Telemetry Failure: Vector search failed. {str(e)}"


@tool("search_web")
def search_web(query: str):
    """MANDATORY for live news, drivers, and team standings."""
    try:
        return get_ddg().invoke(f"{query} Formula 1")
    except Exception as e:
        logger.exception("Web search failed: %s", e)
        return "Search uplink offline. Rely on internal technical specs."


tools = [search_knowledge_base, search_web]

llm = ChatDeepSeek(
    model="deepseek-chat",
    temperature=0,
    api_key=os.getenv("HUGIN"),
    max_retries=2,
    timeout=30,
)
llm_with_tools = llm.bind_tools(tools)


async def agent_node(state: AgentState):
    return {"messages": [await llm_with_tools.ainvoke(state["messages"])]}


async def _run_tool_call(tool_call: dict) -> ToolMessage:
    selected_tool = next((t for t in tools if t.name == tool_call["name"]), None)
    if not selected_tool:
        return ToolMessage(
            content=f"Error: unknown tool {tool_call['name']}",
            name=tool_call["name"],
            tool_call_id=tool_call["id"],
        )
    try:
        res = await asyncio.to_thread(selected_tool.invoke, tool_call["args"])
        return ToolMessage(content=str(res), name=tool_call["name"], tool_call_id=tool_call["id"])
    except Exception as e:
        return ToolMessage(content=f"Error: {str(e)}", name=tool_call["name"], tool_call_id=tool_call["id"])


async def tool_node(state: AgentState):
    """Run every tool call from the last AI message concurrently.

    A comparison query (e.g. "2025 vs 2026") issues one search_knowledge_base
    call per year; running them via asyncio.gather instead of a sequential
    loop means N Pinecone + embedding + rerank round trips overlap instead
    of stacking latency N deep.
    """
    last_message = state["messages"][-1]
    outputs = await asyncio.gather(*(_run_tool_call(tc) for tc in last_message.tool_calls))
    return {"messages": list(outputs)}


def router_function(state: AgentState):
    """Named router for production observability."""
    last_message = state["messages"][-1]
    return "tools" if last_message.tool_calls else END


workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", router_function, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

graph_builder = workflow
