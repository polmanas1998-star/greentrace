"""The analyst's own texts: the promise to check and the documents behind it, pasted as they are.

    {"promise": "100% of our cocoa is deforestation-free since 2021",
     "documents": [{"name": "Supplier sustainability report 2025", "issuer": "supplier", "text": "..."},
                   {"name": "NGO field note", "issuer": "third_party", "text": "..."}]}

issuer says who wrote the document: "supplier" (the company the promise is about: only its own
words can admit clearing) or "third_party" (an NGO, a journalist, an auditor: may name a supplier).
Each text is cut into paragraphs at blank lines (at line breaks when it has no blank line); a
paragraph is what a quote must be found in, word for word. Nothing is summarised or rewritten:
the paragraphs are kept, hashed, in the receipt.
"""
import re
from typing import Any

from app.canonical import sha256_of

MAX_DOCUMENTS, MAX_CHARS, MAX_PARAGRAPHS, MAX_PROMISE = 10, 200_000, 400, 1000
ROLES = {"supplier": "company", "third_party": "third_party"}  # the Evaluator's words for them


class DocumentsRefused(ValueError):
    """The promise or a document cannot be read: the message says which and why."""


def read(promise: Any, documents: Any) -> tuple[str, list[dict], dict[str, str], list[dict]]:
    """(promise, paragraphs as Q1-like rows, doc_id -> role, the documents described) for the pipeline."""
    if not isinstance(promise, str) or not promise.strip():
        raise DocumentsRefused("promise: the claim to check, as text")
    if len(promise) > MAX_PROMISE:
        raise DocumentsRefused(f"promise: at most {MAX_PROMISE} characters")
    documents = documents or []
    if not isinstance(documents, list) or len(documents) > MAX_DOCUMENTS:
        raise DocumentsRefused(f"documents: a list of at most {MAX_DOCUMENTS}")

    paragraphs, roles, described, used = [], {}, [], set()
    for number, document in enumerate(documents, start=1):
        where = f"document {number}"
        if not isinstance(document, dict):
            raise DocumentsRefused(f"{where}: an object with name, issuer and text")
        text, name = document.get("text"), str(document.get("name") or f"document {number}")[:120]
        issuer = document.get("issuer")
        if issuer not in ROLES:
            raise DocumentsRefused(f"{where} ({name}): issuer must be 'supplier' or 'third_party'")
        if not isinstance(text, str) or not text.strip():
            raise DocumentsRefused(f"{where} ({name}): no text")
        if len(text) > MAX_CHARS:
            raise DocumentsRefused(f"{where} ({name}): at most {MAX_CHARS} characters")
        doc_id = _doc_id(name, number, used)
        parts = _paragraphs(text)
        paragraphs += [{"doc_id": doc_id, "section": "main", "paragraph_no": n, "paragraph_text": p}
                       for n, p in enumerate(parts, start=1)]
        roles[doc_id] = ROLES[issuer]
        described.append({"doc_id": doc_id, "name": name, "issuer": issuer, "paragraphs": len(parts),
                          "sha256": sha256_of(text)})
    if len(paragraphs) > MAX_PARAGRAPHS:
        raise DocumentsRefused(f"{len(paragraphs)} paragraphs: at most {MAX_PARAGRAPHS} in all")
    # The promise itself is the first paragraph of its own document, so a narrower scope can be quoted from it.
    paragraphs.insert(0, {"doc_id": "promise", "section": "main", "paragraph_no": 1,
                          "paragraph_text": " ".join(promise.split())})
    roles["promise"] = "company"
    return " ".join(promise.split()), paragraphs, roles, described


def _paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    return [" ".join(block.split()) for block in blocks if block.strip()]


def _doc_id(name: str, number: int, used: set[str]) -> str:
    base = "_".join(re.findall(r"[a-z0-9]+", name.lower()))[:40] or f"doc_{number}"
    doc_id = base if base not in used and base != "promise" else f"{base}_{number}"
    used.add(doc_id)
    return doc_id
