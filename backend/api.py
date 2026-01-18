import os
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

# 1. UPDATED IMPORTS: Use the async saver and graph builder
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from graph import graph_builder 

load_dotenv()

app = FastAPI(title="JANUS F1 MISSION CONTROL", version="2.0.0")

# --- PRODUCTION CORS ---
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
    1. DEFAULT TO 2026: Prioritize new regs.
    2. STRICT ISOLATION: Do not mix years unless comparing.
    3. VISUALS: Use Markdown tables.
    4. CITE: Use [Source: Filename | Year: 20XX].
""")

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    async def event_generator():
        # CRITICAL FIX: Use 'async with' to correctly initialize the Redis connection lifecycle.
        # This yields the actual checkpointer object required by the compiler.
        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
        
        async with AsyncRedisSaver.from_conn_string(REDIS_URL) as memory:
            # Compile the graph using the active checkpointer instance
            compiled_graph = graph_builder.compile(checkpointer=memory)
            
            config = {"configurable": {"thread_id": request.session_id}}
            yield "__LOG__📡 ESTABLISHING UPLINK...\n"
            
            inputs = {"messages": [SYSTEM_PROMPT, HumanMessage(content=request.message)]}
            
            try:
                # Use the stream from the locally compiled graph
                async for chunk in compiled_graph.astream(inputs, config=config, stream_mode="updates"):
                    if "agent" in chunk:
                        msg = chunk["agent"]["messages"][-1]
                        if msg.tool_calls:
                            for t in msg.tool_calls:
                                yield f"__LOG__🔍 ANALYZING {t['args'].get('target_year', 2026)} REGS...\n"
                        elif msg.content:
                            yield msg.content
                    elif "tools" in chunk:
                        yield "__LOG__✅ DATA SECURED.\n"
            except Exception as e:
                import traceback
                print("\n--- [JANUS TELEMETRY FAILURE] ---")
                traceback.print_exc() 
                print("---------------------------------\n")
                yield f"\n[CRITICAL ERROR: {str(e)}]"

    return StreamingResponse(event_generator(), media_type="text/plain")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)