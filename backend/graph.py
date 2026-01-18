import os
import operator
from typing import Annotated, TypedDict, List
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek 
from langchain_pinecone import PineconeVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.tools import DuckDuckGoSearchRun
from pydantic import BaseModel, Field

load_dotenv()

# --- INFRASTRUCTURE ---
if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = PineconeVectorStore(index_name="f1-regulations-all", embedding=embeddings)
ddg = DuckDuckGoSearchRun()

# --- STATE & MODELS ---
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

class SearchInput(BaseModel):
    query: str = Field(description="The technical term to search for.")
    target_year: int = Field(default=2026, description="Specific year to prioritize.")

# --- TOOLS ---
@tool("search_knowledge_base", args_schema=SearchInput)
def search_knowledge_base(query: str, target_year: int = 2026):
    """Accesses FIA F1 Regulations with Batch-Retrieval and Priority-Gated Fallback."""
    try:
        # STRATEGY: One request for current + previous year to eliminate loop latency
        search_years = [target_year, target_year - 1] if target_year > 2022 else [target_year]
        
        results = vectorstore.similarity_search(
            query, 
            k=8, 
            filter={"year": {"$in": search_years}}
        )
        
        if not results:
            return "No relevant regulations found."

        # Sort: Priority 1 (Final) first
        results.sort(key=lambda x: x.metadata.get('priority', 2))
        has_finalized = any(doc.metadata.get('priority') == 1 for doc in results)
        
        context = []
        for doc in results:
            p_val = doc.metadata.get('priority', 2)
            if p_val == 1:
                status = "[[✅ OFFICIAL FINALIZED REGULATION]]"
            else:
                status = "[[⚠️ OBSOLETE DRAFT]]" if has_finalized else "[[ℹ️ PROVISIONAL DRAFT]]"

            context.append(
                f"SOURCE: {doc.metadata.get('source')} | YEAR: {doc.metadata.get('year')} | STATUS: {status}\n"
                f"CONTENT: {doc.page_content}\n"
            )
            
        return "\n---\n".join(context)
    except Exception as e:
        return f"Telemetry Failure: Vector search failed. {str(e)}"

@tool("search_web")
def search_web(query: str):
    """MANDATORY for live news, drivers, and team standings."""
    try:
        return ddg.invoke(f"{query} F1 2026")
    except:
        return "Search uplink offline. Rely on internal technical specs."

tools = [search_knowledge_base, search_web]

# --- NODES ---
llm = ChatDeepSeek(model="deepseek-chat", temperature=0, api_key=os.getenv("HUGIN"), max_retries=2)
llm_with_tools = llm.bind_tools(tools)

def agent_node(state: AgentState):
    return {"messages": [llm_with_tools.invoke(state['messages'])]}

def tool_node(state: AgentState):
    messages = state['messages']
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
    last_message = state['messages'][-1]
    return "tools" if last_message.tool_calls else END

# --- COMPILE ---
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", router_function, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

# REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# memory = AsyncRedisSaver(REDIS_URL) 
# app = workflow.compile(checkpointer=memory)

graph_builder = workflow