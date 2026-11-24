"""Deterministic Year-over-Year filing section diff tool (Item 1A / 1C / 7)."""

from __future__ import annotations

from difflib import SequenceMatcher, unified_diff

from pydantic import BaseModel


class ModifiedParagraph(BaseModel):
    """A paragraph present in both years with material textual edits."""

    similarity: float
    previous_text: str
    current_text: str
    diff_lines: list[str]


class FilingDiffResult(BaseModel):
    """Structured YoY comparison between two fiscal years of the same 10-K section."""

    ticker: str
    item_section: str
    previous_year: int
    current_year: int
    unchanged_count: int
    added_paragraphs: list[str]
    removed_paragraphs: list[str]
    modified_paragraphs: list[ModifiedParagraph]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def diff_section_paragraphs(
    ticker: str,
    item_section: str,
    previous_year: int,
    previous_paragraphs: list[str],
    current_year: int,
    current_paragraphs: list[str],
    match_threshold: float = 0.65,
    unchanged_threshold: float = 0.97,
) -> FilingDiffResult:
    """Compare ordered parent paragraphs between FY(t-1) and FY(t)."""
    prev_clean = [p.strip() for p in previous_paragraphs if p.strip()]
    curr_clean = [p.strip() for p in current_paragraphs if p.strip()]

    matched_prev: set[int] = set()
    unchanged_count = 0
    added: list[str] = []
    modified: list[ModifiedParagraph] = []

    for curr_text in curr_clean:
        best_idx = -1
        best_sim = 0.0
        for idx, prev_text in enumerate(prev_clean):
            if idx in matched_prev:
                continue
            sim = _similarity(prev_text, curr_text)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_idx >= 0 and best_sim >= match_threshold:
            matched_prev.add(best_idx)
            if best_sim >= unchanged_threshold:
                unchanged_count += 1
            else:
                prev_text = prev_clean[best_idx]
                diff = list(
                    unified_diff(
                        prev_text.splitlines(),
                        curr_text.splitlines(),
                        fromfile=f"FY{previous_year}",
                        tofile=f"FY{current_year}",
                        lineterm="",
                    )
                )
                modified.append(
                    ModifiedParagraph(
                        similarity=round(best_sim, 3),
                        previous_text=prev_text,
                        current_text=curr_text,
                        diff_lines=diff,
                    )
                )
        else:
            added.append(curr_text)

    removed = [
        prev_text for idx, prev_text in enumerate(prev_clean) if idx not in matched_prev
    ]

    return FilingDiffResult(
        ticker=ticker.upper(),
        item_section=item_section,
        previous_year=previous_year,
        current_year=current_year,
        unchanged_count=unchanged_count,
        added_paragraphs=added,
        removed_paragraphs=removed,
        modified_paragraphs=modified,
    )
