"""Keep citations tied to the original chunks used by the answer workflow."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


def source_title(text: str, filename: str) -> str:
    for label in ("Document Title", "章节标题", "分段标题"):
        match = re.search(r"^\[" + label + r"\]\s*(.+)$", text[:3000], re.M)
        if match:
            return match.group(1).strip()
    return filename


async def load_query_evidence(response, rag, subject_id):
    chunks = (response.get("data") or {}).get("chunks") or []
    candidates = {
        str(c.get("chunk_id")): c
        for c in chunks
        if isinstance(c, dict) and c.get("chunk_id")
    }
    if not candidates:
        return []
    originals = await rag.text_chunks.get_by_ids(list(candidates))
    document_ids = list(
        dict.fromkeys(
            c.get("full_doc_id") for c in originals if c and c.get("full_doc_id")
        )
    )
    documents = (
        dict(zip(document_ids, await rag.full_docs.get_by_ids(document_ids)))
        if document_ids
        else {}
    )
    evidence = []
    for chunk_id, original in zip(candidates, originals):
        if not original:
            continue
        text = str(original.get("content") or "").strip()
        if not text or str(candidates[chunk_id].get("content") or "").strip() != text:
            continue
        if all(
            re.match(
                r"^\[(?:Document Title|章节标题|分段标题|一级小节|二级小节|关键词|说明)\]",
                line.strip(),
            )
            for line in text.splitlines()
            if line.strip()
        ):
            continue
        document = documents.get(original.get("full_doc_id")) or {}
        # Old token boundaries may have split a UTF-8 character; never display replacements as source text.
        text = text.strip("\ufffd").strip()
        if not text or text not in str(document.get("content") or ""):
            continue
        path = str(document.get("file_path") or original.get("file_path") or "")
        filename = PurePosixPath(path.replace("\\", "/")).name
        if not filename or filename == "unknown_source":
            continue
        evidence.append(
            {
                "subject_id": subject_id,
                "chunk_id": chunk_id,
                "source": filename,
                "title": source_title(str(document.get("content") or ""), filename),
                "text": text,
            }
        )
    return evidence


def answer_evidence(state):
    # Round-robin preserves coverage across subquestions under the prompt budget.
    groups = [
        list(row.get("evidence") or [])
        for row in state.get("subquery_results", [])
        if row.get("query_status") == "success"
    ]
    seen, result, used = set(), [], 0
    while any(groups):
        for group in groups:
            if not group:
                continue
            item = group.pop(0)
            key = (item.get("subject_id"), item.get("chunk_id"))
            if key in seen or not item.get("text"):
                continue
            seen.add(key)
            size = len(item["text"])
            if used + size > 60000 or len(result) >= 24:
                continue
            used += size
            result.append({**item, "id": f"E{len(result) + 1}"})
    return result


def citation_result(answer, evidence):
    known = {item["id"]: item for item in evidence}
    cited = list(dict.fromkeys(re.findall(r"\[(E\d+)\]", answer)))
    invalid = [key for key in cited if key not in known]
    for key in invalid:
        answer = answer.replace(f"[{key}]", "[出处未核实]")
    sources = [{**item, "cited": item["id"] in cited} for item in evidence]
    return answer, {
        "sources": sources,
        "invalidIds": invalid,
        "status": (
            "invalid"
            if invalid
            else "cited" if cited else "uncited" if evidence else "unavailable"
        ),
    }
