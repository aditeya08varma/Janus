import os
import nest_asyncio
import requests
from dotenv import load_dotenv
from llama_parse import LlamaParse
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from langchain_core.documents import Document

# Fix for asyncio loop issues in scripts
nest_asyncio.apply()

load_dotenv()

if os.getenv("MUNIN"):
    os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

# CONFIGURATION 
PINECONE_KEY = os.getenv("MUNIN")
LLAMA_KEY = os.getenv("LLAMA_CLOUD_API_KEY") 
INDEX_NAME = "f1-regulations-all"

if not LLAMA_KEY:
    raise ValueError(" MISSING LLAMA_CLOUD_API_KEY in .env file!")

if not PINECONE_KEY:
    raise ValueError(" MISSING MUNIN (Pinecone Key) in .env file!")

# 1. SETUP PINECONE 
pc = Pinecone(api_key=PINECONE_KEY)

# Create index if it doesn't exist (or clear it if you want a fresh start)
if INDEX_NAME in [i.name for i in pc.list_indexes()]:
    print(f" Clearing old index '{INDEX_NAME}'...")
    pc.delete_index(INDEX_NAME)

print(f" Creating new index '{INDEX_NAME}'...")
pc.create_index(
    name=INDEX_NAME,
    dimension=384,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1")
)

# 2. DOWNLOAD SOURCES 
# We include 2022-2024 so the "Continuity Rule" works (looking back when 2025 is silent).
pdf_urls = {
    "2026_regs.pdf": "https://www.fia.com/sites/default/files/fia_2026_formula_1_technical_regulations_issue_8_-_2024-06-24.pdf",
    "2025_regs.pdf": "https://api.fia.com/sites/default/files/fia_2025_formula_1_technical_regulations_-_issue_01_-_2024-12-11_1.pdf",
    "2024_regs.pdf": "https://www.fia.com/sites/default/files/fia_2024_formula_1_technical_regulations_-_issue_3_-_2023-12-06.pdf",
    "2023_regs.pdf": "https://www.fia.com/sites/default/files/fia_2023_formula_1_technical_regulations_-_issue_1_-_2022-06-29.pdf",
    "2022_regs.pdf": "https://api.fia.com/sites/default/files/formula_1_-_technical_regulations_-_2022_-_iss_11_-_2022-04-29.pdf"
}

for name, url in pdf_urls.items():
    if not os.path.exists(name):
        print(f" Downloading {name}...")
        try:
            r = requests.get(url, timeout=30)
            with open(name, 'wb') as f:
                f.write(r.content)
        except Exception as e:
            print(f" Failed to download {name}: {e}")

# 3. PARSE WITH LLAMAPARSE 
print("👀 Parsing PDFs with LlamaParse (Extracting Tables & Graphs)...")
parser = LlamaParse(
    result_type="markdown", 
    api_key=LLAMA_KEY,
    verbose=True,
    num_workers=4 
)

all_docs = []

# Process PDFs
for filename in pdf_urls.keys():
    if os.path.exists(filename):
        print(f" Reading {filename}...")
        parsed_docs = parser.load_data(filename)
        
        # Extract metadata
        year = filename.split("_")[0]
        era = "Future" if int(year) >= 2026 else "Current"
        
        # Add Metadata to every page
        for doc in parsed_docs:
            lc_doc = Document(
                page_content=doc.text,
                metadata={
                    "source": filename,
                    "year": year,
                    "era": era
                }
            )
            all_docs.append(lc_doc)
    else:
        print(f" Skipping {filename} (not found locally).")

# Process Cheat Sheet (Only if it exists - for specific synonyms/overrides)
if os.path.exists("src/concepts.txt"):
    print("  Reading Cheat Sheet...")
    loader = TextLoader("src/concepts.txt")
    cheat_docs = loader.load()
    for d in cheat_docs: 
        d.metadata["year"] = "General"
        d.metadata["source"] = "CheatSheet"
        d.metadata["era"] = "Universal"
    all_docs.extend(cheat_docs)

# 4. CHUNK & UPLOAD 
print(f" Splitting {len(all_docs)} pages into chunks...")
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
chunks = splitter.split_documents(all_docs)

print(f"Uploading {len(chunks)} chunks to Pinecone...")
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = PineconeVectorStore.from_documents(
    documents=chunks,
    embedding=embeddings,
    index_name=INDEX_NAME
)

print("DONE. The Brain can now read tables natively and remembers the past.")