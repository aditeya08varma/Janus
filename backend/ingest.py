import argparse
import hashlib
import logging
import os
import time

import nest_asyncio
import requests
from dotenv import load_dotenv
from llama_parse import LlamaParse
from langchain_community.document_loaders import TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from langchain_core.documents import Document

from chunking import split_documents_table_aware
from config import CHEATSHEET_SOURCE, EMBEDDING_DIM, EMBEDDING_MODEL, INDEX_NAME

logger = logging.getLogger("janus.ingest")

FINALIZED_MANIFEST = {
    "2026_regs_tech_iss15.pdf",
    "2026_regs_sport_iss04.pdf",
    "2026_regs_operational_iss05.pdf",
    "2026_regs_general_iss01.pdf",
    "2025_regs_tech_iss03.pdf",
    "2024_regs_tech.pdf",
    "2023_regs_tech.pdf",
    "2022_regs.pdf",
}

PDF_URLS = {
    "2026_regs_tech_iss15.pdf": "https://www.fia.com/system/files/documents/fia_2026_f1_regulations_-_section_c_technical_-_iss_15_-_2025-12-10.pdf",
    "2026_regs_sport_iss04.pdf": "https://www.fia.com/system/files/documents/fia_2026_f1_regulations_-_section_b_sporting_-_iss_04_-_2025-12-10.pdf",
    "2026_regs_operational_iss05.pdf": "https://www.fia.com/system/files/documents/fia_2026_f1_regulations_-_section_f_operational_-_iss_05_-_2025-12-10_1.pdf",
    "2026_regs_general_iss01.pdf": "https://www.fia.com/system/files/documents/fia_2026_f1_regulations_-_section_a_general_regulatory_provisions_-_iss_01_-_2025-12-10_0.pdf",
    "2025_regs_tech_iss03.pdf": "https://api.fia.com/sites/default/files/documents/fia_2025_formula_1_technical_regulations_-_issue_03_-_2025-04-07.pdf",
    "2025_regs.pdf": "https://api.fia.com/sites/default/files/fia_2025_formula_1_technical_regulations_-_issue_01_-_2024-12-11_1.pdf",
    "2024_regs_tech.pdf": "https://www.fia.com/sites/default/files/fia_2024_formula_1_technical_regulations_-_issue_3_-_2023-12-06.pdf",
    "2023_regs_tech.pdf": "https://www.fia.com/sites/default/files/fia_2023_formula_1_technical_regulations_-_issue_1_-_2022-06-29.pdf",
    "2022_regs_tech.pdf": "https://www.fia.com/sites/default/files/2022_formula_1_technical_regulations_-_iss_3_-_2021-02-19.pdf",
    "2022_regs.pdf": "https://api.fia.com/sites/default/files/formula_1_-_technical_regulations_-_2022_-_iss_11_-_2022-04-29.pdf",
}


def download_pdfs():
    for name, url in PDF_URLS.items():
        if os.path.exists(name):
            logger.info("Skipping existing %s", name)
            continue
        logger.info("Downloading %s...", name)
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not response.content.startswith(b"%PDF") and "pdf" not in content_type:
                logger.error("Refusing to write %s: response is not a PDF", name)
                continue
            with open(name, "wb") as handle:
                handle.write(response.content)
        except Exception:
            logger.exception("Failed to download %s", name)


def ensure_index(pc: Pinecone, fresh: bool):
    existing = [idx.name for idx in pc.list_indexes()]
    if fresh and INDEX_NAME in existing:
        logger.warning("Deleting index %s (--fresh)", INDEX_NAME)
        pc.delete_index(INDEX_NAME)
        existing = [idx.name for idx in pc.list_indexes()]
    if INDEX_NAME not in existing:
        logger.info("Creating index %s", INDEX_NAME)
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        while True:
            status = pc.describe_index(INDEX_NAME).status
            ready = status.get("ready") if isinstance(status, dict) else getattr(status, "ready", False)
            if ready:
                break
            time.sleep(1)
    else:
        logger.info("Reusing index %s (incremental upsert)", INDEX_NAME)


def tag_section(filename: str) -> str:
    if "tech" in filename:
        return "Technical"
    if "sport" in filename:
        return "Sporting"
    if "operational" in filename:
        return "Operational"
    if "general" in filename:
        return "General"
    return "Technical"


def parse_documents(llama_key: str) -> list:
    parser = LlamaParse(
        result_type="markdown",
        api_key=llama_key,
        num_workers=4,
        parsing_instruction=(
            "This is an F1 Technical/Sporting Regulation. Extract all tables precisely in Markdown. "
            "Preserve every Article number (e.g., C3.4.1) at the start of its paragraph. "
            "Do not omit technical units (kg, mm, kW)."
        ),
    )
    all_docs = []
    for filename in PDF_URLS:
        if not os.path.exists(filename):
            logger.warning("Skipping %s (not found locally)", filename)
            continue
        logger.info("Reading and tagging %s", filename)
        parsed_docs = parser.load_data(filename)
        doc_year = int(filename.split("_")[0])
        section = tag_section(filename)
        priority = 1 if filename in FINALIZED_MANIFEST else 2
        for doc in parsed_docs:
            all_docs.append(
                Document(
                    page_content=doc.text,
                    metadata={
                        "source": filename,
                        "year": doc_year,
                        "section": section,
                        "priority": priority,
                        "era": "Nimble Car" if doc_year >= 2026 else "Ground Effect",
                    },
                )
            )
    if os.path.exists("concepts.txt"):
        cheat_docs = TextLoader("concepts.txt").load()
        for doc in cheat_docs:
            doc.metadata.update({
                "year": 0,
                "source": CHEATSHEET_SOURCE,
                "priority": 1,
                "section": "Glossary",
                "era": "Universal",
            })
        all_docs.extend(cheat_docs)
    return all_docs


def chunk_id(doc: Document, index: int) -> str:
    src = doc.metadata.get("source", "unknown")
    digest = hashlib.sha256(f"{src}:{index}:{doc.page_content[:120]}".encode()).hexdigest()
    return digest[:32]


def upsert_chunks(pc: Pinecone, chunks: list):
    index = pc.Index(INDEX_NAME)
    sources = {doc.metadata.get("source") for doc in chunks if doc.metadata.get("source")}
    for source in sources:
        try:
            index.delete(filter={"source": {"$eq": source}})
            logger.info("Removed prior vectors for %s", source)
        except Exception:
            logger.exception("Could not delete prior vectors for %s", source)
    ids = [chunk_id(doc, i) for i, doc in enumerate(chunks)]
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    PineconeVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
        index_name=INDEX_NAME,
        ids=ids,
    )


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    nest_asyncio.apply()
    load_dotenv()
    if os.getenv("MUNIN"):
        os.environ["PINECONE_API_KEY"] = os.getenv("MUNIN")

    parser = argparse.ArgumentParser(description="Ingest FIA regulations into Pinecone")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete and recreate the entire index. Default is incremental upsert per source file.",
    )
    args = parser.parse_args(argv)

    pinecone_key = os.getenv("MUNIN")
    llama_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not llama_key:
        raise ValueError("MISSING LLAMA_CLOUD_API_KEY in .env file!")
    if not pinecone_key:
        raise ValueError("MISSING MUNIN (Pinecone Key) in .env file!")

    pc = Pinecone(api_key=pinecone_key)
    ensure_index(pc, fresh=args.fresh)
    download_pdfs()
    all_docs = parse_documents(llama_key)
    logger.info("Splitting %s pages with table-aware chunking", len(all_docs))
    chunks = split_documents_table_aware(all_docs)
    logger.info("Upserting %s chunks", len(chunks))
    upsert_chunks(pc, chunks)
    logger.info("DONE. Janus 2.0 knowledge base updated.")


if __name__ == "__main__":
    main()
