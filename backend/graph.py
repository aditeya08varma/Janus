import logging
import operator
import os
from typing import Annotated, TypedDict, List

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langchain_community.tools import DuckDuckGoSearchRun
from pydantic import BaseModel, Field

from config import DEFAULT_YEAR, EMBEDDING_MODEL, INDEX_NAME, RETRIEVAL_K
from retrieval import format_context, rerank_docs, retrieve_with_cascade, sort_results
from year_extract import resolve_search_years

load_dotenv()
logger = logging.getLogger(__name__)

if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

_vectorstore = None
_ddg = None


def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        from langchain_pinecone import PineconeVectorStore
        from langchain_huggingface import HuggingFaceEmbeddings

        embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
        _vectorstore = PineconeVectorStore(index_name=INDEX_NAME, embedding=embeddings)
    return _vectorstore


def get_ddg():
    global _ddg
    if _ddg is None:
        _ddg = DuckDuckGoSearchRun()
    return _ddg


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

llm = ChatDeepSeek(model="deepseek-chat", temperature=0, api_key=os.getenv("HUGIN"), max_retries=2)
llm_with_tools = llm.bind_tools(tools)


def agent_node(state: AgentState):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


def tool_node(state: AgentState):
    messages = state["messages"]
    last_message = messages[-1]
    outputs = []
    for tool_call in last_message.tool_calls:
        selected_tool = next((t for t in tools if t.name == tool_call["name"]), None)
        if selected_tool:
            try:
                res = selected_tool.invoke(tool_call["args"])
                outputs.append(ToolMessage(content=str(res), name=tool_call["name"], tool_call_id=tool_call["id"]))
            except Exception as e:
                outputs.append(ToolMessage(content=f"Error: {str(e)}", name=tool_call["name"], tool_call_id=tool_call["id"]))
    return {"messages": outputs}


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
