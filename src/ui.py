import streamlit as st
import os
import time
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from graph import app

load_dotenv()

# 1. PAGE CONFIGURATION
st.set_page_config(
    page_title="JANUS | F1 2026 Telemetry",
    page_icon="🏎️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. ADVANCED CSS 
st.markdown("""
<style>
    /* Main Background & Text */
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    
    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background-color: #161B22;
        border-right: 1px solid #30363D;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #FF1E1E !important; /* Ferrari Red */
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 700;
    }
    
    /* Chat Message Bubbles */
    .stChatMessage {
        background-color: #1F242D;
        border: 1px solid #30363D;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    
    /* Input Field Styling */
    .stChatInputContainer {
        border-color: #FF1E1E;
    }
    
    /* Telemetry Badges (Custom HTML) */
    .status-badge {
        display: inline-block;
        padding: 6px 12px;
        border-radius: 6px;
        font-size: 0.85em;
        font-weight: 600;
        font-family: 'Courier New', monospace;
        letter-spacing: 1px;
        margin-bottom: 10px;
    }
    .status-thinking { background-color: #238636; color: white; border: 1px solid #2EA043; }
    .status-searching { background-color: #1F6FEB; color: white; border: 1px solid #388BFD; }
    .status-error { background-color: #DA3633; color: white; border: 1px solid #F85149; }
    .status-success { background-color: #A371F7; color: white; border: 1px solid #BC8CFF; }

    /* Custom Button Styling */
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        border: 1px solid #FF1E1E;
        background-color: transparent;
        color: #FF1E1E;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #FF1E1E;
        color: white;
        border-color: #FF1E1E;
    }
</style>
""", unsafe_allow_html=True)

# 3. SIDEBAR: MISSION CONTROL 
with st.sidebar:
    st.title("🏎️ JANUS 2.0")
    st.caption("Technical Regulations Intelligence")
    st.markdown("---")
    
    st.subheader("🛠️ Pit Wall Controls")
    
    # Preset Buttons
    if st.button("🚀 Explain Active Aero (X-Mode)"):
        st.session_state.prompt_trigger = "Explain how the Active Aerodynamics (X-Mode vs Z-Mode) works in 2026."
    
    if st.button("🔋 MGU-K Manual Override"):
        st.session_state.prompt_trigger = "How does the Manual Override mode work for the MGU-K?"
        
    if st.button("⛽ Sustainable Fuel Rules"):
        st.session_state.prompt_trigger = "What are the requirements for 100% sustainable fuel in 2026?"
        
    st.markdown("---")
    
    # System Status Indicators
    st.markdown("**Telemetry Status**")
    c1, c2 = st.columns(2)
    with c1:
        st.success("BRAIN: V3")
    with c2:
        st.info("EYES: LLAMA")
    
    st.markdown("---")
    if st.button("🗑️ Clear Telemetry (Reset)"):
        st.session_state.messages = []
        st.rerun()

# 4. SESSION STATE 
if "messages" not in st.session_state:
    st.session_state.messages = []

# Handle Button Clicks
if "prompt_trigger" in st.session_state and st.session_state.prompt_trigger:
    prompt = st.session_state.prompt_trigger
    del st.session_state.prompt_trigger 
else:
    prompt = st.chat_input("Radio check...Enter query: e.g., 'What is the max wheelbase?'")

SYSTEM_PROMPT = SystemMessage(content="""
    You are the **F1 Technical Director (Transition Specialist)** covering the shift from the "Ground Effect Era" (2022-2025) to the "Nimble Car Era" (2026).
    
    ### CORE PRIME DIRECTIVES
    
    1. **THE "TIME BARRIER" (CRITICAL):**
       - **2022-2025:** Strictly adhere to Ground Effect rules. NEVER apply a 2026 rule to a 2025 question.
       - **2026:** Use strictly 2026 documents.
       - **Ambiguity:** If the user asks about a specific year, answer ONLY for that year.

    2. **THE "CONTINUITY" PROTOCOL:**
       - **ALGORITHM:** If a value is missing in 2025 -> Check 2024 -> Check 2023 -> Check 2022.
       - **CITATION:** State clearly that the value is carried over from a previous year.

    3. **CAPABILITY VS. LEGALITY (SPORTING CONTEXT):**
       - **Manual Override (MOM):** Technically provides 350kW boost, but is **prohibited** for race leaders defending position (Sporting Regs).
       - **MGU-H:** Component **REMOVED** in 2026.

    ### TOOL UTILIZATION STRATEGY
    
    - **ROUTE: 'search_web'** for: People, Teams, News, Rumors.
    - **ROUTE: 'search_knowledge_base'** for: Dimensions, Aerodynamics, Engine specs.
    - **ANTI-LOOPING:** If a search fails, broaden the query. Do not retry the exact same term.
    
    ### KNOWLEDGE PRIORITY
    1. **Cheat Sheet:** ABSOLUTE TRUTH.
    2. **2026 Regulations:** For 2026 queries.
    3. **2025 Regulations:** For current queries.
    4. **2022-2024 Regulations:** Fallback for continuity.

    ### FORMATTING
    - **Tone:** Engineering Professional. Terse, data-heavy.
    - **Format:** Bullet points for specs. Markdown Tables for comparisons.
    - **Citations:** [Source: Article X.Y | Year: 20XX].
""")

# SYSTEM_PROMPT = SystemMessage(content="""
#     You are a Formula 1 Technical Director expert in the transition from the 2025 to 2026 Regulations.
    
#     TOOL ROUTING (MANDATORY):
#     1. **"Who" / People / Teams:** YOU MUST USE 'search_web'.
#        - Example: "Who are the Audi drivers?", "Is Ford joining Red Bull?"
#     2. **"What" / Technical / Specs:** USE 'search_knowledge_base'.
#        - Example: "What is the max wheelbase?", "Explain X-Mode."

#     THE "TIME DIRECTION" RULE (CRITICAL):
#     - **2025 Questions:** You can ONLY use 2025, 2024, or 2022 documents. **NEVER** use a 2026 rule to answer a 2025 question. (2026 is the future; it cannot change the past).
#     - **2026 Questions:** Use 2026 documents.
#     - **Comparison:** Cite both eras clearly.

#     THE "REGULATORY CONTINUITY" RULE:
#     - The 2022-2025 regulations belong to the **same generation** (Ground Effect Era).
#     - **IF** you cannot find a specific number in the 2025 text (e.g., "Wheelbase"), but you see it in the 2024 or 2022 text:
#     - **YOU MUST** assume the rule has not changed.
#     - **ANSWER:** "The limit is [Value], carried over from the [Year] regulations."

#     KNOWLEDGE BASE PRIORITY:
#     1. **Cheat Sheet:** If you find data in 'Concept Definition' (Table Data or Graphs), treat it as **ABSOLUTE FACT** (it overrides PDFs).
#     2. **2026 Docs:** Use ONLY for 2026 questions.
#     3. **2025 Docs:** Primary source for current cars.
#     4. **2022-2024 Docs:** Fallback source. Use these if 2025 is silent.

#     TERMINOLOGY MAPPING (2026):
#     - "X-Mode" = "Straight Mode" (Low Drag).
#     - "Z-Mode" = "Corner Mode" (High Downforce).
#     - "Manual Override" = "Overtake Mode" (350kW MGU-K Boost).

#     STYLE GUIDELINES:
#     1. Be technical but clear. Explain the 'Why' (Physics) and the 'What' (Regulation).
#     2. Cite specific Article numbers and the **YEAR** of the document (e.g., "Article 5.1 (2024 Regs)").
#     3. Use proper terms: "MGU-K", "ICE", "State of Charge".

#     INSTRUCTIONS:
#     1. **NO LOOPING:** If you search the knowledge base once and don't find the answer, **DO NOT** search the exact same query again. Try a broader search or admit you don't know.
#     2. **Cite Sources:** Mention the Year of the document you found the data in.
# """)

#  6. DISPLAY 
st.markdown("### 📡 Live Telemetry Feed")
for message in st.session_state.messages:
    if isinstance(message, HumanMessage):
        with st.chat_message("user", avatar="🏎️"):
            st.markdown(message.content)
    elif isinstance(message, AIMessage):
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(message.content)

# THE AI LOOP 
if prompt:
    # A. Display User Message
    st.session_state.messages.append(HumanMessage(content=prompt))
    with st.chat_message("user", avatar="🏎️"):
        st.markdown(prompt)

    # B. Run the Agent
    with st.chat_message("assistant", avatar="🤖"):
        # Live Feedback Containers
        status_box = st.empty()
        response_box = st.empty()
        source_expander = st.expander("📄 View Source Documents (Raw Data)", expanded=False)
        
        inputs = {"messages": [SYSTEM_PROMPT] + st.session_state.messages}
        full_response = ""
        raw_sources = ""
        
        try:
            status_box.markdown("<div class='status-badge status-thinking'>🧠 INITIALIZING NEURAL LINK...</div>", unsafe_allow_html=True)
            
            for event in app.stream(inputs):
                
                # Event 1: Tool Usage (Searching...)
                if "tools" in event:
                    tool_msg = event["tools"]["messages"][0]
                    tool_name = tool_msg.name
                    raw_data = tool_msg.content
                    
                    # Update status badge
                    status_box.markdown(f"<div class='status-badge status-searching'>🔎 SCANNING REGULATIONS: {tool_name.upper()}</div>", unsafe_allow_html=True)
                    
                    # Capture source for the expander
                    raw_sources += f"\n\n--- SOURCE: {tool_name} ---\n{raw_data[:800]}..." # Truncate for UI
                
                # Event 2: Agent Thinking/Answering
                if "agent" in event:
                    msg = event["agent"]["messages"][0]
                    if not msg.tool_calls:
                        full_response = msg.content
            
            # C. Final Display
            status_box.empty() # Clear the status badge
            response_box.markdown(full_response)
            
            # Show Sources if available
            if raw_sources:
                source_expander.code(raw_sources, language="markdown")
            else:
                source_expander.info("Answer generated from internal logic (no new search required).")

            # Save to history
            st.session_state.messages.append(AIMessage(content=full_response))

        except Exception as e:
            status_box.markdown(f"<div class='status-badge status-error'>❌ TELEMETRY FAILURE: {str(e)}</div>", unsafe_allow_html=True)