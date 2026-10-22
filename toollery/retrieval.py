from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import math
import re
from typing import Protocol
import unicodedata

from .data import ToolPool
from .storage import digest

TOKENIZER_VERSION = "unicode_identifier_cjk_v1"
REPRESENTATIONS = ("spec", "manual", "spec_manual")


def tokenize(text: str) -> list[str]:
    # Split identifiers before case folding; split CJK runs into chars and bigrams.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", unicodedata.normalize("NFKC", text))
    words = re.findall(r"[^\W_]+", text.casefold(), re.UNICODE)
    output = []
    for word in words:
        for part in re.findall(r"[\u3400-\u9fff]+|[^\u3400-\u9fff]+", word):
            if "\u3400" <= part[0] <= "\u9fff":
                output.extend(part)
                output.extend(part[i:i + 2] for i in range(len(part) - 1))
            else:
                output.append(part)
    return output


def build_documents(pool: ToolPool, representation: str, manuals: dict[str, dict] | None = None) -> dict[str, str]:
    if representation not in REPRESENTATIONS:
        raise ValueError(f"Invalid representation: {representation}")
    if representation != "spec" and (manuals is None or set(manuals) != set(pool.tool_ids)):
        raise ValueError("Manual retrieval requires valid coverage of the entire pool")
    documents = {}
    for tool_id in pool.tool_ids:
        tool = pool.tools[tool_id]
        if representation == "spec":
            text = tool.spec_text
        else:
            proxy_text = "\n".join(manuals[tool_id]["manual"]["proxy_queries"])
            # The shared name is already present in raw spec; do not repeat it in combined mode.
            text = (tool.name + "\n" + proxy_text) if representation == "manual" else (tool.spec_text + "\n" + proxy_text)
        documents[tool_id] = text
    return documents


@dataclass(frozen=True)
class RankedTool:
    tool_id: str
    score: float


class Retriever(Protocol):
    def search(self, query: str, limit: int | None = 10) -> list[RankedTool]: ...


class BM25Index:
    def __init__(self, documents: dict[str, str], *, k1: float = 1.5, b: float = 0.75):
        if not documents or not math.isfinite(k1) or not math.isfinite(b) or k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid BM25 corpus or parameters")
        self.k1, self.b = k1, b
        self.tool_ids = tuple(sorted(documents))
        postings = defaultdict(list)
        lengths = []
        for index, tool_id in enumerate(self.tool_ids):
            terms = Counter(tokenize(documents[tool_id]))
            lengths.append(sum(terms.values()))
            for term, count in terms.items():
                postings[term].append((index, count))
        self.postings = dict(postings)
        self.average_length = sum(lengths) / len(lengths) or 1.0
        self.length_norms = [k1 * (1 - b + b * size / self.average_length) for size in lengths]
        self.metadata = {
            "algorithm": "bm25", "k1": k1, "b": b,
            "tokenizer_version": TOKENIZER_VERSION, "query_term_frequency": "binary",
            "tie_break": "tool_id_ascending", "top_k_zero_score_policy": "omit",
            "document_count": len(documents), "document_hash": digest(documents),
            "exact_name_boost": False,
        }

    def search(self, query: str, limit: int | None = 10) -> list[RankedTool]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive, or None for all")
        scores = defaultdict(float)
        size = len(self.tool_ids)
        for term in sorted(set(tokenize(query))):
            matches = self.postings.get(term, ())
            idf = math.log1p((size - len(matches) + 0.5) / (len(matches) + 0.5))
            for index, frequency in matches:
                scores[index] += idf * frequency * (self.k1 + 1) / (frequency + self.length_norms[index])
        key = lambda pair: (-pair[1], self.tool_ids[pair[0]])
        if limit is None:
            ordered = sorted(scores.items(), key=key)
            ranked = [RankedTool(self.tool_ids[index], score) for index, score in ordered]
            ranked.extend(RankedTool(tool_id, 0.0) for index, tool_id in enumerate(self.tool_ids) if index not in scores)
            return ranked
        ordered = heapq.nsmallest(min(limit, size), scores.items(), key=key)
        return [RankedTool(self.tool_ids[index], score) for index, score in ordered]


class ToolSearch:
    """Search returns original records separately from the retrieval-only documents."""

    def __init__(self, pool: ToolPool, index: Retriever):
        self.pool, self.index = pool, index

    def select(self, prompt: str, *, budget: str = "top_k", k: int = 10) -> list[RankedTool]:
        if budget not in {"all", "top_k"}:
            raise ValueError("budget must be all or top_k")
        return self.index.search(prompt, limit=None if budget == "all" else k)

    def original_tools(self, ranked: list[RankedTool]):
        return [self.pool.tools[hit.tool_id] for hit in ranked]
