# 🏎️ Project Documentation: JANUS 2.0

**Status**: Active / Deployment Ready  
**Engine**: DeepSeek-V3 (Hugin)  
**Database**: Pinecone (Munin)

---

## 1. 🏎️ JANUS 2.0: A Bridge Between F1 Eras

### "Why Janus?"
In Roman mythology, **Janus** is the god of transitions, depicted with two faces—one looking back at the past and the other looking toward the future. 

I chose this name because Formula 1 is currently standing at its own crossroads. As we move from the high-downforce "Ground Effect" cars of 2022-2025 into the "Nimble Car" era of 2026, the sport is essentially reinventing itself. I engineered this project to be that bridge: a technical partner that understands where we’ve been and exactly where we are going.

### 🧠 The Thought & The Memory
To give Janus a "brain," I looked to Norse mythology. Odin had two ravens that traveled the world to bring him truth:

* **Hugin (Thought)**: This is the reasoning engine. I named the **DeepSeek-V3** implementation Hugin because it handles the complex technical "thinking" and logic gates.
* **Munin (Memory)**: This is the **Pinecone Vector Database**. It acts as the system's long-term memory, holding the raw data of the FIA regulations so they are never forgotten or misinterpreted.

---

## 🎯 The Story Behind the Code

This project started with a personal challenge: *"Can I build a RAG system that is actually reliable?"*

I didn't want to just follow a tutorial and build another generic chatbot. I wanted to build something for people like me—F1 enthusiasts who have endless questions but don't want to spend their weekends digging through 100-page PDF files of professional technical jargon just to find a single spec.

**The Problem: The Hallucination Gap**
If you ask a standard LLM about a niche F1 rule, it will often "hallucinate" an answer that *sounds* confident but is technically wrong. In a sport where a 1mm difference in a wing endplate can lead to a disqualification, "close enough" is a failure. 

**The Janus Mission: Truth over Creativity**
I built this to be a high-fidelity technical partner. Instead of "training" a model to guess, I’ve **engineered a RAG pipeline** that anchors every response in official FIA documentation. I have prioritized **fact-grounding over generative flair**: if the data isn't in the regulations, Janus is programmed to tell you it doesn't know rather than making up a fact. 

For a true F1 enthusiast, accuracy is the only metric that matters.

---

## 2. System Architecture
The project is structured as a **Monorepo**, separating the high-speed streaming engine from the specialized telemetry dashboard.

### 📂 Directory Structure
```text
JANUS/
├── backend/             # Python FastAPI Service
│   ├── api.py           # REST API & Cache Logic
│   ├── graph.py         # LangGraph State Machine
│   ├── ingest.py        # Data Ingestion Tools
│   ├── requirements.txt # Backend Dependencies
│   └── janus_cache.json # Persistent Memory
├── frontend/            # React 19 / Vite UI
│   ├── src/             # Application Logic (App.jsx)
│   ├── package.json     # Frontend Dependencies
│   └── .env.local       # Local Environment Config
└── .gitignore           # Master ignore (Root)
```

---

## 3. Technical Specifications

### Core Protocols
- **The Time Barrier**: Hard-coded logic gates ensure 2026 specs are never hallucinated into 2025 queries.
- **Continuity Protocol**: An automated fallback algorithm that checks 2022–2024 regulations when 2025 data points are carried over without explicit mention in newer documents.
- **Persistent LRU Cache**: A localized JSON-based cache that stores up to 100 technical briefings, enabling instant retrieval for repeat queries.

### Tech Stack
| Tier | Technology |
|------|------------|
| LLM | DeepSeek-V3 (Hugin) |
| RAG | Pinecone Vector DB (Munin) |
| Frameworks | LangGraph, FastAPI, React 19 |
| Styling | Tailwind CSS 4 |

----

## 4. Installation & Deployment Guide

### Phase 1: Local Environment Setup

#### Backend Initialization
Navigate to the backend directory:
```bash
cd backend
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Boot the API:
```bash
python api.py
```

#### Frontend Initialization
Navigate to the frontend directory:
```bash
cd frontend
```

Install packages:
```bash
npm install
```

Launch development server:
```bash
npm run dev
```

### Phase 2: Production Deployment

#### Backend (Railway)
- Deploy the `/backend` folder.
- Mount a **Volume** to `/backend` to ensure `janus_cache.json` persists.

#### Frontend (Vercel)
- Deploy the `/frontend` folder.
- Link via the `VITE_API_URL` environment variable.

---

## 5. Maintenance & Support

### Updating Specifications
To update the knowledge base with new official FIA releases:
1. Place new PDFs in the `data` folder.
2. Run the ingestion script:
```bash
python backend/ingest.py
```

### Troubleshooting
- **Telemetry Loss**: Usually caused by CORS mismatches or missing environment keys.
- **Cache Misses**: Ensure `janus_cache.json` has write permissions in your hosting environment.
