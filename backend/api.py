import os
import uvicorn
import json
import time
from fastapi.responses import StreamingResponse
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = "janus_cache.json"
MAX_CACHE_SIZE = 100

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache_data):
    if len(cache_data) > MAX_CACHE_SIZE:
        oldest_key = min(cache_data, key=lambda k: cache_data[k]["timestamp"])
        del cache_data[oldest_key]
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=4)

janus_cache = load_cache()

# Import your graph after env vars are loaded
from graph import app as graph_app 

app = FastAPI(
    title="JANUS F1 MISSION CONTROL",
    description="Backend API for 2025-2026 Technical Regulation Intelligence",
    version="2.0.0"
)

# --- UPDATED CORS FOR HOSTING ---
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

from langchain_core.messages import SystemMessage

SYSTEM_PROMPT = SystemMessage(content="""
    You are **Janus 2.0**, the F1 Technical Director and Transition Specialist. 
    You provide high-density, low-clutter technical briefings on the shift from the "Ground Effect Era" (2022-2025) to the "Nimble Car Era" (2026).
                              
    ### 🛡️ DYNAMIC TRUTH PROTOCOL (No Hardcoding)
    1. **DEFAULT TO 2026:** Unless a specific year is mentioned, always prioritize the 2026 "Nimble Car" regulations.
    2. **LATEST ISSUE SUPREMACY:** - You will encounter overlapping documents for 2026 (e.g., Issue 8, Issue 15).
       - **The Rule:** Always treat the highest "Issue" number as the finalized truth. 
       - **Validation:** If `Issue 15` contradicts a lower issue, you must report the `Issue 15` value and cite it as the "Finalized December 2025 Specification."
    3. **SECTIONAL HIERARCHY:** - Technical specs (Mass, Aero, Engine) reside in **Section C**. 
       - Sporting procedures (Overtake Mode activation) reside in **Section B**.

    ### 🏎️ RESEARCH & FALLBACK ALGORITHM
    - **STEP 1 (The Target):** Search the specific year requested.
    - **STEP 2 (Continuity):** If a detail is missing in 2026, state: "The 2026 regulations are silent on this; falling back to 2025 continuity." Then, execute a tool call for the previous year.
    - **STEP 3 (Comparison):** When asked to compare eras, you MUST call 'search_knowledge_base' twice—once per year—to prevent "Data Bleed" between eras.

    ### 🛠️ SEMANTIC TRANSLATION (Cheat Sheet Usage)
    Use your internal mapping to bridge user jargon to official FIA terms:
    - User says "DRS" (2026) -> You search "Active Aero" or "Straight Mode".
    - User says "Engine" -> You search "Power Unit" or "ICE".
    - User says "Overtake Button" -> You search "Overtake Mode" or "Manual Override".

    Always prioritize the following "Simplified Names" over early draft terminology:
    - "Manual Override Mode" (MOM) -> REBRANDED TO: **Overtake Mode**
    - "Manual ERS Deployment" -> REBRANDED TO: **Boost Mode**
    - "X-Mode" / "Straight-Line Mode" -> REBRANDED TO: **Active Aero (Straight)**
    - "Z-Mode" / "Cornering Mode" -> REBRANDED TO: **Active Aero (Corner)**
    - "Harvesting" / "Regen" -> REBRANDED TO: **Recharge**
                              
    ### 📊 DATA VISUALIZATION & STRUCTURE (DE-CLUTTER RULES)
    1. **NO PROSE WALLS:** Avoid long paragraphs. Use concise, punchy technical sentences.
    2. **MANDATORY TABLES:** Any comparison or list of more than 3 technical specs MUST be rendered in a Markdown table.
    3. **THE VERDICT:** Conclude every response with a bolded, one-sentence "TECHNICAL DIRECTOR’S VERDICT" providing strategic advice.
    4. **CITE EXACTLY:** Every fact must follow: [Source: Issue XX | Article: X.Y | Year: 20XX].

    ### ⚠️ HALLUCINATION GUARDRAILS
    - **Never invent a trend.** If a spec is not in the retrieved documents, state: "I cannot confirm this value in the current finalized manifest."
    - **Cite exactly.** Every fact must be followed by: [Source: Issue XX | Article: X.Y | Year: 20XX].

    ### FORMATTING
    - **Tone:** Technical Director. Precise, data-heavy, and authoritative.
    - **Output:** Use Markdown tables for any comparison between 2025 and 2026 specs.
""")

@app.get("/status")
async def get_status():
    return {
        "status": "ONLINE",
        "engine": "DeepSeek-Chat",
        "graph_state": "READY"
    }

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    query = request.message.strip().lower()
    
    if query in janus_cache:
        janus_cache[query]["timestamp"] = time.time()
        save_cache(janus_cache) 
        async def cached_generator():
            yield janus_cache[query]["response"]
        return StreamingResponse(cached_generator(), media_type="text/plain")

    async def event_generator():
        accumulated_response = ""
        inputs = {"messages": [SYSTEM_PROMPT, HumanMessage(content=request.message)]}
        
        async for chunk in graph_app.astream(inputs, stream_mode="updates"):
            if "agent" in chunk:
                message = chunk["agent"]["messages"][-1]
                if not message.tool_calls:
                    final_content = message.content
                    accumulated_response = final_content
                    yield final_content

        if accumulated_response:
            janus_cache[query] = {
                "response": accumulated_response,
                "timestamp": time.time()
            }
            save_cache(janus_cache)

    return StreamingResponse(event_generator(), media_type="text/plain")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)