import os
from dotenv import load_dotenv
from typing import Annotated, TypedDict, List
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_pinecone import PineconeVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.tools import DuckDuckGoSearchRun
import operator

# SETUP 
load_dotenv()
os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

#  CONNECT TO MUNIN (MEMORY) 
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = PineconeVectorStore(
    index_name="stripe-knowledge-base",
    embedding=embeddings
)

# DEFINE THE STATE 
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

# DEFINE THE TOOLS 
@tool
def search_knowledge_base(query: str):
    """
    Useful for answering questions about Stripe technical documentation,
    PaymentIntents, Webhooks, or API specific details.
    """
    print(f" ACCESSING MUNIN: Searching for '{query}' ")
    
    results = vectorstore.similarity_search(query, k=3)
    
    if not results:
        print(" MUNIN RETURNED EMPTY ")
        return "No relevant documentation found."

    print(f"--- MUNIN FOUND {len(results)} CHUNKS ---")
    
    context = "\n\n".join([doc.page_content for doc in results])
    return context

@tool
def search_web(query: str):
    """
    Useful for finding general info, current events, or things NOT in the Stripe docs.
    """
    print(f"ACCESSING WEB: Searching for '{query}' ")
    search = DuckDuckGoSearchRun()
    return search.invoke(query)

tools = [search_knowledge_base, search_web]

# INITIALIZE HUGIN (THE BRAIN)
# We use Llama 3.3. 
# If you get "tool_use_failed" errors, it's because the model is being too creative with XML.
# The 'agent_node' below fixes this by forcing strict JSON instructions.
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0,
    api_key=os.getenv("HUGIN")
)
llm_with_tools = llm.bind_tools(tools)

# DEFINE THE NODES 

def agent_node(state: AgentState):
    print(" HUGIN IS THINKING ")
    messages = state['messages']
    
    # SYSTEM HACK: Force Llama 3 to behave
    # We prepend a system message if it's not there to enforce tool format.
    if not isinstance(messages[0], SystemMessage):
        hack_message = SystemMessage(content="You are a helpful assistant. You must answer the user's question directly or call a tool. If you call a tool, you must use standard JSON format. Do not use XML tags.")
        messages = [hack_message] + messages
    
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def router_function(state: AgentState):
    last_message = state['messages'][-1]
    if last_message.tool_calls:
        return "tools"
    return END

# BUILD THE GRAPH 
workflow = StateGraph(AgentState)

workflow.add_node("agent", agent_node)
workflow.add_node("tools", ToolNode(tools))

workflow.set_entry_point("agent")

workflow.add_edge("tools", "agent")
workflow.add_conditional_edges(
    "agent",
    router_function,
    {
        "tools": "tools",
        END: END
    }
)

app = workflow.compile()

# TEST RUNNER 
if __name__ == "__main__":
    print("\n>>> TEST: Stripe Docs Query")
    
    user_input = "What are the common reasons a PaymentIntent might fail?"
    
    # We add a SystemMessage to give the agent a 'Role'
    system_prompt = SystemMessage(content="""
        You are Janus, an expert Stripe integration assistant.
        1. Use the 'search_knowledge_base' tool to find answers in the documentation.
        2. If the tool returns context, USE IT to answer the user.
        3. Do not search for the same thing multiple times.
        4. If the answer is not in the context, say "I don't know."
    """)
    
    inputs = {"messages": [system_prompt, HumanMessage(content=user_input)]}
    
    try:
        final_result = app.invoke(inputs, config={"recursion_limit": 10})
        print(f"\nJanus: {final_result['messages'][-1].content}")
    except Exception as e:
        print(f"Error: {e}")