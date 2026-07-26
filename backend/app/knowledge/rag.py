"""
rag.py — lightweight retrieval over the Qadri Group knowledge documents
(01-08, 10-12, 14-16, 18: the PDF/markdown business/architecture docs).

Design choice: pure lexical TF-IDF retrieval (scikit-learn), not an
embeddings/vector-DB pipeline. Reasons:
  * The corpus is small (~15 short documents) — a linear TF-IDF scan is
    already sub-millisecond, no ANN index needed.
  * It works with zero OpenAI dependency/cost/latency, so retrieval never
    breaks even if the LLM key is missing or rate-limited.
  * This domain is terminology-heavy (exact words like "Sailing", "On
    Water", "Available Amoun") where lexical matching is at least as good
    as semantic embeddings, if not better.
Corpus is built once per process (module-level singleton) from the PDFs/MD
files in rag_documents/, chunked one-chunk-per-PDF-page (or per top-level
markdown section for .md files).

IMPORTANT — these documents describe the ORIGINAL PLANNING/DESIGN of the
system (an idealized schema). The live database schema has since diverged
in places (see app/knowledge/business_rules.py, which is verified against
the REAL database and is the sole authority for SQL generation). RAG output
here is used only as supplementary business/terminology context — never as
a source of column names or SQL logic. Callers must keep that framing when
inserting retrieved text into a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_DOCS_DIR = Path(__file__).resolve().parent / "rag_documents"


@dataclass
class Chunk:
    doc: str       # source filename, e.g. "05_Imports_Knowledge.pdf"
    section: str   # page number or markdown heading, for citation
    text: str


def _chunk_pdf(path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            chunks.append(Chunk(doc=path.name, section=f"page {i}", text=text))
    return chunks


def _chunk_markdown(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    # split on top-level (## ) headings; keep the heading with its body
    parts = re.split(r"\n(?=#{1,3}\s)", raw)
    chunks: list[Chunk] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        first_line = part.splitlines()[0].lstrip("#").strip()
        chunks.append(Chunk(doc=path.name, section=first_line or "intro", text=part))
    return chunks or [Chunk(doc=path.name, section="full", text=raw)]


def _load_corpus() -> list[Chunk]:
    if not _DOCS_DIR.exists():
        return []
    chunks: list[Chunk] = []
    for path in sorted(_DOCS_DIR.iterdir()):
        if path.suffix.lower() == ".pdf":
            chunks.extend(_chunk_pdf(path))
        elif path.suffix.lower() == ".md":
            chunks.extend(_chunk_markdown(path))
    return chunks


class RagIndex:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = _load_corpus()
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        if self.chunks:
            self._vectorizer = TfidfVectorizer(stop_words="english", max_df=0.9)
            self._matrix = self._vectorizer.fit_transform([c.text for c in self.chunks])

    @property
    def ready(self) -> bool:
        return bool(self.chunks) and self._vectorizer is not None

    def retrieve(self, query: str, k: int = 3, min_score: float = 0.05) -> list[Chunk]:
        """Top-k most lexically similar chunks to `query`. Empty if the
        corpus isn't loaded or nothing clears `min_score`."""
        if not self.ready or not query.strip():
            return []
        q_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self._matrix)[0]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out: list[Chunk] = []
        for i in ranked[:k]:
            if scores[i] >= min_score:
                out.append(self.chunks[i])
        return out

    def format_for_prompt(self, chunks: list[Chunk]) -> str:
        if not chunks:
            return ""
        blocks = [f"[{c.doc} — {c.section}]\n{c.text}" for c in chunks]
        return "\n\n".join(blocks)


# Module-level singleton — built once per process.
_index: RagIndex | None = None


def get_index() -> RagIndex:
    global _index
    if _index is None:
        _index = RagIndex()
    return _index
