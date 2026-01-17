import os
from dotenv import load_dotenv
from typing import Annotated, TypedDict, List
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, ToolMessage, AIMessage, HumanMessage
from langchain_deepseek import ChatDeepSeek 
from langchain_core.tools import tool
from langchain_pinecone import PineconeVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.tools import DuckDuckGoSearchRun
from langgraph.checkpoint.memory import MemorySaver # <--- ADDED MEMORY
from pydantic import BaseModel, Field
import operator
import redis
from langgraph.checkpoint.redis import RedisSaver

load_dotenv()

if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = PineconeVectorStore(index_name="f1-regulations-all", embedding=embeddings)
ddg = DuckDuckGoSearchRun()

class SearchInput(BaseModel):
    query: str = Field(description="The technical term to search for.")
    target_year: int = Field(default=2026, description="The specific year to search.")

class WebSearchInput(BaseModel):
    query: str = Field(description="Query for live news or driver info.")

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

@tool("search_knowledge_base", args_schema=SearchInput)
def search_knowledge_base(query: str, target_year: int = 2026):
    """Accesses official FIA F1 Regulations with Priority-Gated Fallback."""
    search_years = list(range(target_year, 2021, -1))
    accumulated_context = ""
    
    for year in search_years:
        results = vectorstore.similarity_search(query, k=4, filter={"year": year})
        
        if results:
            # --- THE FIX: SORT & LABEL ---
            # 1. Sort: Priority 1 (Final) first, Priority 2 (Draft) last
            results.sort(key=lambda x: x.metadata.get('priority', 2))
            
            has_finalized = any(doc.metadata.get('priority') == 1 for doc in results)
            
            for doc in results:
                priority_val = doc.metadata.get('priority', 2)
                
                # 2. Label: Explicitly warn the LLM about drafts
                if priority_val == 1:
                    status_label = "[[✅ OFFICIAL FINALIZED REGULATION - PRIMARY SOURCE]]"
                else:
                    status_label = "[[⚠️ OBSOLETE DRAFT - IGNORE IF CONFLICTS]]" if has_finalized else "[[ℹ️ PROVISIONAL DRAFT]]"

                accumulated_context += (
                    f"--- REGULATION SEGMENT ---\n"
                    f"SOURCE: {doc.metadata.get('source')} | YEAR: {year} | STATUS: {status_label}\n"
                    f"CONTENT: {doc.page_content}\n\n"
                )
            
            if has_finalized:
                print(f"✅ FINALIZED DATA FOUND FOR {year}. STOPPING SEARCH.")
                break

    return accumulated_context or "No relevant regulations found."

@tool("search_web", args_schema=WebSearchInput)
def search_web(query: str):
    """MANDATORY for Drivers, Teams, and News."""
    return ddg.invoke(f"{query} F1 2026")

tools = [search_knowledge_base, search_web]

llm = ChatDeepSeek(
    model="deepseek-chat",
    temperature=0,
    api_key=os.getenv("HUGIN"),
    max_retries=2
)
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
            tool_result = selected_tool.invoke(tool_call["args"])
            outputs.append(ToolMessage(content=str(tool_result), name=tool_call["name"], tool_call_id=tool_call["id"]))
    return {"messages": outputs}

def router_function(state: AgentState):
    last_message = state['messages'][-1]
    if not last_message.tool_calls:
        return END
    return "tools"

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", router_function, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

# --- THE FIX: COMPILE WITH MEMORY ---
REDIS_URL = os.getenv("REDIS_URL")

# 3. FIX: Pass the connection using the 'conn' keyword specifically
memory = RedisSaver(REDIS_URL) 

app = workflow.compile(checkpointer=memory)