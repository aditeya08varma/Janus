"""Table-aware splitting: keep markdown table headers with their rows."""

import re
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import CHUNK_OVERLAP, CHUNK_SIZE, TABLE_CHUNK_SIZE

TABLE_BLOCK_RE = re.compile(r"(?:^|\n)((?:\|[^\n]+\|\s*\n?)+)", re.MULTILINE)
SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|\s*$")


def split_table_preserving_header(table_text: str, max_size: int = TABLE_CHUNK_SIZE) -> List[str]:
    lines = [ln for ln in table_text.strip().split("\n") if ln.strip()]
    if not lines:
        return []
    joined = "\n".join(lines)
    if len(joined) <= max_size:
        return [joined]

    header = [lines[0]]
    data_start = 1
    if len(lines) > 1 and SEPARATOR_RE.match(lines[1]):
        header.append(lines[1])
        data_start = 2
    header_text = "\n".join(header)

    chunks: List[str] = []
    current = header_text
    for line in lines[data_start:]:
        candidate = f"{current}\n{line}"
        if len(candidate) > max_size and current != header_text:
            chunks.append(current)
            current = f"{header_text}\n{line}"
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_documents_table_aware(docs: List[Document]) -> List[Document]:
    prose = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    out: List[Document] = []

    for doc in docs:
        text = doc.page_content or ""
        matches = list(TABLE_BLOCK_RE.finditer(text))
        if not matches:
            out.extend(prose.split_documents([doc]))
            continue

        pos = 0
        for match in matches:
            pre = text[pos : match.start()]
            if pre.strip():
                for piece in prose.split_text(pre):
                    out.append(Document(page_content=piece, metadata=dict(doc.metadata)))
            for table_chunk in split_table_preserving_header(match.group(1)):
                meta = dict(doc.metadata)
                meta["chunk_type"] = "table"
                out.append(Document(page_content=table_chunk, metadata=meta))
            pos = match.end()
        rest = text[pos:]
        if rest.strip():
            for piece in prose.split_text(rest):
                out.append(Document(page_content=piece, metadata=dict(doc.metadata)))

    return out
