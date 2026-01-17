import os
import uvicorn
import redis
import re
import hashlib
from fastapi.responses import StreamingResponse
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()

# --- REDIS CACHE KEY LOGIC ---
def get_canonical_key(query: str):
    q = query.lower().strip()
    # Era Partitioning (Regex logic for isolation)
    year = "2026"
    # If explicit mention of 2025/current, use 2025 bucket.
    # Logic: "2025" exists AND "2026" does NOT exist -> 2025.
    # Otherwise default to 2026 (handles "Compare 2025 and 2026" safely)
    if "2025" in q and "2026" not in q:
        year = "2025"
    elif re.search(r"current|ground effect|25", q) and not re.search(r"2026|future|nimble|26", q):
        year = "2025"
        
    # Semantic Normalization
    q = q.replace("weight", "mass").replace("engine", "power unit").replace("drs", "active aero")
    intent_hash = hashlib.md5(q.encode()).hexdigest()
    return f"janus:{year}:{intent_hash}"

# --- CONFIGURATION ---
redis_client = redis.from_url(
    os.getenv("REDIS_URL"),
    decode_responses=True
)

# Import the graph (Ensure graph.py has the MemorySaver fix applied!)
from graph import app as graph_app 

app = FastAPI(
    title="JANUS F1 MISSION CONTROL",
    description="Backend API for 2025-2026 Technical Regulation Intelligence",
    version="2.0.0"
)

# --- CORS ---
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- UPDATED REQUEST MODEL (MULTI-USER SUPPORT) ---
class ChatRequest(BaseModel):
    message: str
    # Frontend sends a UUID here. If missing, defaults to "demo" (Single User fallback)
    session_id: str = "demo_global_session"

# --- SYSTEM PROMPT (Can be injected into graph state or passed here) ---
SYSTEM_PROMPT = SystemMessage(content="""
    You are **Janus 2.0**, the F1 Technical Director.
    
    ### TRUTH PROTOCOL
    1. **DEFAULT TO 2026:** Prioritize "Nimble Car" regs unless told otherwise.
    2. **STRICT ISOLATION:** If asked about 2025, do NOT mix in 2026 rules unless comparing.
    3. **VISUALS:** Use Markdown tables for comparisons.
    
    ### FALLBACK LOGIC
    - Search current year first. If silent, check previous year for continuity.
    - Cite sources: [Source: Issue XX | Year: 20XX].
    
    ### WARNINGS
    - If you see [[⚠️ OBSOLETE DRAFT]], warn the user heavily.
""")

@app.get("/status")
async def get_status():
    try:
        redis_status = "CONNECTED" if redis_client.ping() else "ERROR"
    except Exception as e:
        redis_status = "OFFLINE"
    return {
        "status": "ONLINE",
        "engine": "DeepSeek-Chat",
        "memory": f"Redis ({redis_status})",
        "mode": "STATEFUL (MemorySaver Active)"
    }

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    # 1. CACHE CHECK (The "Fast Path")
    # We still cache based on the QUERY, not the session. 
    # Logic: "What is the weight" is the same answer for everyone.
    # Note: Complex follow-ups ("And for the engine?") won't hit this cache 
    # because they are unique strings. This is expected behavior.
    target_key = get_canonical_key(request.message)
    
    try:
        cached_response = redis_client.get(target_key)
        if cached_response:
            print(f"⚡ CACHE HIT: {target_key}")
            async def cached_generator():
                yield cached_response
            return StreamingResponse(cached_generator(), media_type="text/plain")
    except Exception as e:
        print(f"⚠️ Redis Error: {e}")

    # 2. GRAPH EXECUTION (The "Smart Path")
    async def event_generator():
        accumulated_response = ""
        
        # --- MULTI-USER CONFIGURATION ---
        # This tells LangGraph to load the specific history for THIS session_id
        config = {"configurable": {"thread_id": request.session_id}}
        
        # We only pass the NEW message. LangGraph retrieves history from MemorySaver.
        # We also pass SystemPrompt as a distinct message to ensure it persists/refreshes.
        inputs = {"messages": [SYSTEM_PROMPT, HumanMessage(content=request.message)]}
        
        # Pass 'config' to astream to enable Memory
        async for chunk in graph_app.astream(inputs, config=config, stream_mode="updates"):
            
            if "agent" in chunk:
                message = chunk["agent"]["messages"][-1]
                
                # CASE A: Tool Calls (Send Logs)
                if message.tool_calls:
                    for tool in message.tool_calls:
                        if tool['name'] == "search_knowledge_base":
                            # Send special log line for Frontend HUD
                            yield f"__LOG__🔍 INSPECTING REGULATIONS: {tool['args'].get('target_year')} ERA\n"
                            yield f"__LOG__📡 QUERY: '{tool['args'].get('query')}'\n"
                
                # CASE B: Final Answer (Send Content)
                elif message.content:
                    accumulated_response = message.content
                    yield message.content

            # CASE C: Tool Execution Updates
            elif "tools" in chunk:
                 yield f"__LOG__✅ DATA SECURED. ANALYZING...\n"
        
        # Cache the final result (if it wasn't a conversation fragment)
        if accumulated_response and len(accumulated_response) > 20:
             redis_client.set(target_key, accumulated_response, ex=86400)

    return StreamingResponse(event_generator(), media_type="text/plain")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)