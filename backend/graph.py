import os
from dotenv import load_dotenv
from typing import Annotated, TypedDict, List
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, ToolMessage, AIMessage
from langchain_deepseek import ChatDeepSeek 
from langchain_core.tools import tool
from langchain_pinecone import PineconeVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.tools import DuckDuckGoSearchRun
from pydantic import BaseModel, Field
import operator

load_dotenv()

# FIX : MAP KEY FOR PINECONE 
if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

# 1. SETUP MEMORY
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

vectorstore = PineconeVectorStore(
    index_name="f1-regulations-all",
    embedding=embeddings
)

# 2. DEFINE TOOLS
ddg = DuckDuckGoSearchRun()


class SearchInput(BaseModel):
    query: str = Field(description="The technical term or rule to search for (e.g., 'minimum mass').")
    target_year: int = Field(
        default=2026, 
        description="The specific year of regulations to search. Defaults to 2026. Use 2025 for current era questions."
    )

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

# @tool("search_knowledge_base", args_schema=SearchInput)
# def search_knowledge_base(query: str):
#     """
#     Useful for technical questions about F1 Regulations (Aero, Engine, Weight).
#     """
#     print(f" ACCESSING MUNIN: Searching for '{query}' ")
#     try:
#         results = vectorstore.similarity_search(query, k=6)
#         if not results:
#             return "No relevant regulations found."
        
#         context = ""
#         for doc in results:
#             source = doc.metadata.get('source', 'Unknown')
#             year = doc.metadata.get('year', 'Unknown')
#             era = doc.metadata.get('era', 'Unknown') 
#             context += f"[Source: {source} | Year: {year} | Era: {era}]\n{doc.page_content}\n\n"
#         return context
#     except Exception as e:
#         return f"Error accessing knowledge base: {str(e)}"


@tool("search_knowledge_base", args_schema=SearchInput)
def search_knowledge_base(query: str, target_year: int = 2026):
    """
    Directly accesses official FIA F1 Regulations (Technical, Sporting, Operational).
    Uses a hierarchical fallback: checks the requested year first, then falls back 
    to previous years (2025, 2024, etc.) to ensure continuity of data.
    """
    print(f" ACCESSING MUNIN: Searching for '{query}' starting in {target_year}")
    
    # 1. Define the search order (e.g., 2026 -> 2025 -> 2024 -> 2023 -> 2022)
    search_years = list(range(target_year, 2021, -1))
    accumulated_context = ""
    
    try:
        for year in search_years:
            # Metadata filter forces Pinecone to only look at this specific year
            results = vectorstore.similarity_search(
                query, 
                k=4, 
                filter={"year": year}
            )
            
            if results:
                for doc in results:
                    source = doc.metadata.get('source', 'Unknown')
                    priority = doc.metadata.get('priority', 2)
                    section = doc.metadata.get('section', 'Unknown')
                    
                    # Format for the LLM to easily parse
                    accumulated_context += (
                        f"--- REGULATION SEGMENT ---\n"
                        f"SOURCE: {source}\n"
                        f"YEAR: {year}\n"
                        f"SECTION: {section}\n"
                        f"PRIORITY: {'FINALIZED' if priority == 1 else 'DRAFT/ARCHIVE'}\n"
                        f"CONTENT: {doc.page_content}\n\n"
                    )
                
                # If we found a finalized Priority 1 document for the year, 
                # we stop the loop to prevent "Data Bleed" from older eras.
                if any(res.metadata.get('priority') == 1 for res in results):
                    break

        if not accumulated_context:
            return "RESULT: No relevant regulations found. The requested spec may have been renamed or removed."

        return accumulated_context

    except Exception as e:
        return f"CRITICAL ERROR: Knowledge base access failed: {str(e)}"

# Update your tools list

class WebSearchInput(BaseModel):
    query: str = Field(description="The search query for live F1 news or driver data.")

@tool("search_web", args_schema=WebSearchInput)
def search_web(query: str):
    """
    MANDATORY for Drivers, Teams, and News.
    """
    print(f"ACCESSING WEB: Searching for '{query}' ")
    try:
        raw_results = ddg.invoke(f"{query} F1 2026")
        print(f" WEB RESULT LENGTH: {len(raw_results)} chars ")
        return f" VERIFIED WEB SEARCH RESULTS \n{raw_results}\n"
    except Exception as e:
        return f"Error searching web: {str(e)}"

tools = [search_knowledge_base, search_web]

# 3. INITIALIZE HUGIN (The Brain)
llm = ChatDeepSeek(
    model="deepseek-chat",
    temperature=0,
    api_key=os.getenv("HUGIN"),
    max_retries=2
)
llm_with_tools = llm.bind_tools(tools)

# 4. GRAPH NODES 

def agent_node(state: AgentState):
    print(" HUGIN (DEEPSEEK) IS THINKING ")
    messages = state['messages']
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def tool_node(state: AgentState):
    """
    Manually executes the tools requested by the LLM.
    This replaces the 'ToolNode' import that was crashing.
    """
    messages = state['messages']
    last_message = messages[-1]
    
    outputs = []
    
    # Iterate through all tool calls the AI wants to make
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        
        # Find the matching tool in our list
        selected_tool = next((t for t in tools if t.name == tool_name), None)
        
        if selected_tool:
            print(f" EXECUTING TOOL: {tool_name}")
            try:
                # Run the tool
                tool_result = selected_tool.invoke(tool_args)
            except Exception as e:
                tool_result = f"Error executing tool: {e}"
            
            # Create a ToolMessage 
            outputs.append(ToolMessage(
                content=str(tool_result),
                name=tool_name,
                tool_call_id=tool_call["id"]
            ))
        else:
            outputs.append(ToolMessage(
                content=f"Error: Tool {tool_name} not found.",
                name=tool_name,
                tool_call_id=tool_call["id"]
            ))
            
    return {"messages": outputs}

def finalize_search(state: AgentState):
    """
    FORCED STOP: This node runs when the agent tries to search too many times.
    It intercepts the tool call and returns a system message forcing an answer.
    """
    print(" MAX SEARCH LIMIT REACHED - FORCING ANSWER")
    last_message = state['messages'][-1]
    outputs = []
    
    # We must respond to the tool calls to satisfy the LLM structure,
    # but we give it a "Stop" command instead of real data.
    for tool_call in last_message.tool_calls:
        outputs.append(ToolMessage(
            content="SYSTEM ALERT: Maximum search steps reached. You have sufficient information. STOP SEARCHING. Synthesize your final answer immediately based on what you already know.",
            name=tool_call["name"],
            tool_call_id=tool_call["id"]
        ))
    
    return {"messages": outputs}

def router_function(state: AgentState):
    messages = state['messages']
    last_message = messages[-1]
    
    # If no tool calls, we are done.
    if not last_message.tool_calls:
        return END
    
    # Count how many times the AI has already replied (iterations)
    # Each 'pair' of AI Message + Tool Message is one step.
    ai_actions = [m for m in messages if isinstance(m, AIMessage) and m.tool_calls]
    count = len(ai_actions)
    
    print(f" Search Iteration: {count} / 7")
    
    # Limit Logic
    if count >= 7:
        return "finalize"  
    
    return "tools"

# 5. BUILD GRAPH 
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node) 
workflow.add_node("finalize", finalize_search)

workflow.set_entry_point("agent")


workflow.add_conditional_edges("agent", router_function, {"tools": "tools", "finalize": "finalize",END: END})
workflow.add_edge("tools", "agent")
workflow.add_edge("finalize", "agent")
app = workflow.compile()