import os
from dotenv import load_dotenv
from langchain_community.document_loaders import WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore


load_dotenv()
pinecone_api_key = os.getenv("MUNIN")

if not pinecone_api_key:
    raise ValueError("MUNIN key not found in .env file!")

# THE FIX: Map MUNIN to the default variable name 
os.environ["PINECONE_API_KEY"] = pinecone_api_key


# Define the "Knowledge Source" (Stripe Payments)
urls = [
    "https://docs.stripe.com/payments/payment-intents",
    "https://docs.stripe.com/webhooks",
    "https://docs.stripe.com/api/payment_intents"
]

print(f" HUGIN IS SCOUTING {len(urls)} STRIPE PAGES ")
loader = WebBaseLoader(urls)
docs = loader.load()
print(f"Loaded {len(docs)} pages.")

# 3. Chunk the Data
print("SPLITTING TEXT INTO CHUNKS ")
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200
)
splits = text_splitter.split_documents(docs)
print(f"Created {len(splits)} total knowledge chunks.")

# 4. Initialize Embeddings
print(" INITIALIZING LOCAL EMBEDDINGS")
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

# 5. Setup Pinecone
print("CONNECTING TO MUNIN (PINECONE)")
# We don't strictly need to re-initialize 'pc' here for the upload, 
# but we keep it to ensure the index exists.
pc = Pinecone(api_key=pinecone_api_key)
index_name = "stripe-knowledge-base"

# Check if index exists, if not, create it
existing_indexes = [index.name for index in pc.list_indexes()]

if index_name not in existing_indexes:
    print(f"Creating new index: {index_name}...")
    pc.create_index(
        name=index_name,
        dimension=384,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1")
    )
    print("Index created!")
else:
    print(f"Index '{index_name}' already exists.")

# 6. Upload Data
print("] FEEDING MUNIN (UPLOADING VECTORS)")
# Now this will work because we set os.environ["PINECONE_API_KEY"]
vectorstore = PineconeVectorStore.from_documents(
    documents=splits,
    embedding=embeddings,
    index_name=index_name
)

print("SUCCESS! Munin has remembered the data.")