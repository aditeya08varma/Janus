# 🏎️ Janus 2.0: F1 Technical Regulation Intelligence

**Status**: 🟢 Production / Live  
**Engine**: DeepSeek-V3 (Hugin)  
**Memory**: Pinecone Vector DB (Munin)  
**Cache**: Redis High-Speed Key-Value

---

## 1. 🏎️ Janus 2.0: A Bridge Between F1 Eras

### "Why Janus?"
In Roman mythology, **Janus** is the god of transitions, depicted with two faces—one looking back at the past and the other looking toward the future.

I chose this name because Formula 1 is currently standing at its own crossroads. As we move from the high-downforce **"Ground Effect"** cars of **2022–2025** into the **"Nimble Car"** era of **2026**, the sport is essentially reinventing itself. I engineered this project to be that bridge: a technical partner that understands where we’ve been and exactly where we are going.

### 🧠 The Thought & The Memory
To give Janus a "brain," I looked to Norse mythology. Odin had two ravens that traveled the world to bring him truth:

- **Hugin (Thought)**: The **DeepSeek-V3** reasoning engine. I named the implementation Hugin because it handles complex technical thinking, logic gates, and agentic tool use via **LangGraph**.
- **Munin (Memory)**: The **Pinecone Vector Database**. It acts as the system's long-term memory, holding the raw data of the official FIA regulations so they are never forgotten or misinterpreted.

---

## 🎯 The Story Behind the Code

### The Problem: The Hallucination Gap
In a sport where a **1mm** difference in a wing endplate can lead to disqualification, "close enough" is a failure. Standard LLMs often hallucinate niche rules that sound confident but are technically incorrect.

### The Janus Mission: Truth over Creativity
I engineered a **RAG (Retrieval-Augmented Generation) pipeline** that anchors every response in official FIA documentation. I prioritized **fact-grounding over generative flair**: if the data isn't in the regulations, Janus is programmed to report that it doesn't know rather than making up a fact.

---

## 2. System Architecture
The project is structured as a **monorepo**, separating the high-speed streaming engine from the specialized telemetry dashboard.

### 📂 Directory Structure
```text
JANUS/
├── backend/             # Python FastAPI Service
│   ├── api.py           # REST API & Redis Cache Logic
│   ├── graph.py         # LangGraph State Machine
│   ├── ingest.py        # Data Ingestion Tools
│   ├── Dockerfile       # Container with Pre-baked Brain
│   └── requirements.txt # Backend Dependencies
└── frontend/            # React 19 / Vite UI
    ├── src/App.jsx      # Application Logic & HUD
    ├── package.json     # Frontend Dependencies
    └── vite.config.js   # Production Build Config
```

---

## 3. Technical Specifications

### Core Protocols
- **Semantic Translation**: Bridges user jargon to official FIA terminology (e.g., `"DRS in 2026"` → `"Active Aero"` or `"X/Z Mode"`).
- **Hierarchical Fallback**: Automated continuity checks across **2022–2025** when 2026 data points are carried over without explicit mention in newer documents.
- **Docker "Pre-Bake"**: To prevent cold-start timeouts on the server, the embedding model (**all-MiniLM-L6-v2**) is downloaded during the image build process.

### Tech Stack
| Tier | Technology |
|------|------------|
| LLM | DeepSeek-V3 (Hugin) |
| Vector DB | Pinecone (Munin) |
| Orchestration | LangGraph & FastAPI |
| Memory / Cache | Redis (Render Key-Value) |
| UI | React 19 + Tailwind CSS 4 |

---

## 4. Installation & Deployment Guide

### Phase 1: Local Environment Setup

#### Backend Initialization
```bash
cd backend
pip install -r requirements.txt
uvicorn api:app --reload
```

#### Frontend Initialization
```bash
cd frontend
npm install
npm run dev
```

### Phase 2: Production Deployment

#### Backend (Render Web Service)
- Containerized via **Docker** and hosted on **Render** as a Web Service.
- Requires environment keys:
  - `REDIS_URL`
  - `HUGIN`
  - `MUNIN`

#### Frontend (Render Static Site)
- Hosted as a **Static Site** on Render.
- Connected via `VITE_API_URL` to ensure cross-origin telemetry streaming.

---

## 5. System Logic Flow

### Data Routing Workflow
1. **Driver Query**: User enters a question (e.g., "How is 2026 power different?").
2. **Semantic Mapping**: System translates query terms to FIA technical nomenclature.
3. **Parallel Search**: Agentic tools query Pinecone across multiple regulatory "Issues" to find finalized truth.
4. **Telemetry Stream**: Search logs are streamed to the UI in real-time as the agent thinks.
5. **Final Briefing**: Answer is synthesized in Markdown tables and cached in Redis for instant repeat retrieval.

### Technical Director's Verdict
**Janus 2.0 represents a production-ready implementation of an agentic RAG system tailored for high-accuracy sporting regulations.**
