"""SEC 10-K HTML section extractor and parent-child chunker."""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from pydantic import BaseModel

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

TARGET_SECTIONS: tuple[str, ...] = ("Item 1", "Item 1A", "Item 1C", "Item 7", "Item 8")
TARGET_10Q_SECTIONS: tuple[str, ...] = ("Item 1", "Item 2", "Item 1A")

# Matches headings like "ITEM 1A. RISK FACTORS", "Item 7 - MD&A", or table-formatted "| Item 1A. | Risk Factors |"
_ITEM_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\|\s*)?(item\s+(1a|1b|1c|1|2|3|4|5|6|7a|7|8|9a|9b|9|10|11|12|13|14|15))\b[\s.\-:\u2013\u2014|]*([^\n]{0,120})",
    re.IGNORECASE,
)

# Fallback headings for integrated 10-K filings (e.g. INTC) that omit "Item X" prefixes in body headings.
_FALLBACK_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:\|\s*)?("
    r"fundamentals of our business|company overview"
    r"|risk factors"
    r"|cybersecurity"
    r"|management['\u2019]s discussion and analysis(?: of financial condition and results of operations)?"
    r"|financial statements and supplem[a-z ]+"
    r"|quantitative and qualitative disclosures about market risk"
    r"|unresolved staff comments"
    r"|exhibits and financial statement schedules"
    r")\s*(?:\|\s*)*$",
    re.IGNORECASE | re.MULTILINE,
)

_FALLBACK_TITLE_TO_ITEM: tuple[tuple[str, str], ...] = (
    ("fundamentals of our business", "Item 1"),
    ("company overview", "Item 1"),
    ("risk factors", "Item 1A"),
    ("cybersecurity", "Item 1C"),
    ("management", "Item 7"),
    ("financial statements", "Item 8"),
)


class ParentChunk(BaseModel):
    """Full paragraph or table block stored in `narrative_parents`."""

    item_section: str
    section_title: str
    parent_index: int
    content: str


class ChildChunk(BaseModel):
    """Precision retrieval snippet stored in `narrative_chunks`."""

    item_section: str
    parent_index: int
    chunk_index: int
    content: str


def _table_to_markdown(table: Tag) -> str:
    """Convert an HTML <table> into a compact Markdown pipe table."""
    rows: list[str] = []
    for tr in table.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", td.get_text(" ", strip=True))
            for td in tr.find_all(["th", "td"])
        ]
        non_empty = [c for c in cells if c]
        if non_empty:
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def html_to_clean_text(html: str) -> str:
    """Convert SEC 10-K HTML (including inline XBRL) into clean text with preserved tables."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()

    # Replace HTML tables with Markdown blocks before text extraction
    for table in soup.find_all("table"):
        md = _table_to_markdown(table)
        table.replace_with(f"\n{md}\n" if md else "\n")

    raw_text = soup.get_text("\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
    # Collapse excessive blank lines while keeping paragraph breaks
    cleaned_lines: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                cleaned_lines.append("")
            prev_blank = True
        else:
            cleaned_lines.append(line)
            prev_blank = False
    return "\n".join(cleaned_lines).strip()


def _canonical_item_label(raw_item: str) -> str:
    """Normalize 'item  1a' -> 'Item 1A'."""
    suffix = re.sub(r"^item\s+", "", raw_item.strip(), flags=re.IGNORECASE).upper()
    return f"Item {suffix}"


def _map_fallback_heading(heading: str) -> str | None:
    lowered = heading.strip().lower()
    for prefix, item_label in _FALLBACK_TITLE_TO_ITEM:
        if lowered.startswith(prefix):
            return item_label
    return None


def _extract_fallback_title_sections(
    text: str,
    target_set: set[str],
) -> dict[str, tuple[str, str]]:
    """Fallback extractor for integrated 10-K filings that omit 'Item X' prefixes in body headings."""
    matches = list(_FALLBACK_SECTION_RE.finditer(text))
    if not matches:
        return {}

    best_spans: dict[str, tuple[int, str, str]] = {}
    for idx, match in enumerate(matches):
        raw_title = match.group(1).strip(" .-\u2013\u2014|")
        item_label = _map_fallback_heading(raw_title)
        if item_label is None or item_label not in target_set:
            continue
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        prev = best_spans.get(item_label)
        if prev is None or len(body) > prev[0]:
            best_spans[item_label] = (len(body), raw_title or item_label, body)

    return {
        label: (title, body)
        for label, (length, title, body) in best_spans.items()
        if length >= 500
    }


def extract_10k_sections(
    html: str,
    target_sections: tuple[str, ...] = TARGET_SECTIONS,
) -> dict[str, tuple[str, str]]:
    """Extract {item_section: (section_title, section_text)} from 10-K HTML.

    When an Item heading appears both in the Table of Contents and in the filing body,
    selects the occurrence with the largest body span so TOC entries are skipped.
    """
    text = html_to_clean_text(html)
    target_set = set(target_sections)
    best_spans: dict[str, tuple[int, str, str]] = {}

    matches = list(_ITEM_HEADING_RE.finditer(text))
    for idx, match in enumerate(matches):
        item_label = _canonical_item_label(match.group(1))
        if item_label not in target_set:
            continue

        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        title = match.group(3).strip(" .-\u2013\u2014|") or item_label

        # Skip tiny Table-of-Contents stubs when a fuller body exists
        prev = best_spans.get(item_label)
        if prev is None or len(body) > prev[0]:
            best_spans[item_label] = (len(body), title, body)

    extracted = {
        label: (title, body)
        for label, (length, title, body) in best_spans.items()
        if length >= 80
    }

    # If primary Item-heading extraction missed sections (e.g. integrated 10-Ks like INTC),
    # fill missing sections via standalone canonical SEC section title headings.
    missing_targets = {
        label
        for label in target_set
        if label not in extracted or len(extracted[label][1]) < 300
    }
    if missing_targets:
        fallback = _extract_fallback_title_sections(text, missing_targets)
        for label, (title, body) in fallback.items():
            if label not in extracted or len(body) > len(extracted[label][1]):
                extracted[label] = (title, body)

    return extracted


def _split_oversized_paragraph(para: str, max_chars: int) -> list[str]:
    """Subdivide a single oversized block on newlines, sentences, or word boundaries."""
    if len(para) <= max_chars:
        return [para]

    pieces: list[str] = []
    # First split by single newlines (common in SEC HTML filings)
    lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
    buf = ""
    for line in lines:
        if len(line) > max_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            # Split long single line on sentence boundaries, then hard-wrap on spaces
            sentences = re.split(r"(?<=[.!?])\s+", line)
            s_buf = ""
            for sent in sentences:
                if len(sent) > max_chars:
                    if s_buf:
                        pieces.append(s_buf)
                        s_buf = ""
                    pos = 0
                    while pos < len(sent):
                        end = min(pos + max_chars, len(sent))
                        if end < len(sent):
                            space_idx = sent.rfind(" ", pos, end)
                            if space_idx > pos + max_chars // 2:
                                end = space_idx
                        pieces.append(sent[pos:end].strip())
                        pos = end
                        while pos < len(sent) and sent[pos] == " ":
                            pos += 1
                elif not s_buf:
                    s_buf = sent
                elif len(s_buf) + 1 + len(sent) <= max_chars:
                    s_buf = f"{s_buf} {sent}"
                else:
                    pieces.append(s_buf)
                    s_buf = sent
            if s_buf:
                pieces.append(s_buf)
        elif not buf:
            buf = line
        elif len(buf) + 1 + len(line) <= max_chars:
            buf = f"{buf}\n{line}"
        else:
            pieces.append(buf)
            buf = line
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p]


def chunk_sections(
    sections: dict[str, tuple[str, str]],
    parent_max_chars: int = 1400,
    child_max_chars: int = 400,
    child_overlap_chars: int = 60,
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """Split extracted sections into parent paragraphs and overlapping child chunks."""
    if child_overlap_chars >= child_max_chars:
        raise ValueError("child_overlap_chars must be smaller than child_max_chars.")

    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []

    for item_section, (section_title, body) in sections.items():
        raw_paragraphs: list[str] = []
        for block in re.split(r"\n\s*\n", body):
            cleaned = block.strip()
            if cleaned:
                raw_paragraphs.extend(_split_oversized_paragraph(cleaned, parent_max_chars))
        merged_parents: list[str] = []
        buffer = ""

        for para in raw_paragraphs:
            if not buffer:
                buffer = para
            elif len(buffer) + 2 + len(para) <= parent_max_chars:
                buffer = f"{buffer}\n\n{para}"
            else:
                merged_parents.append(buffer)
                buffer = para
        if buffer:
            merged_parents.append(buffer)

        for p_idx, p_text in enumerate(merged_parents):
            parents.append(
                ParentChunk(
                    item_section=item_section,
                    section_title=section_title,
                    parent_index=p_idx,
                    content=p_text,
                )
            )

            # Slice parent into overlapping child chunks for precision retrieval
            step = child_max_chars - child_overlap_chars
            c_idx = 0
            pos = 0
            while pos < len(p_text):
                snippet = p_text[pos : pos + child_max_chars].strip()
                if snippet:
                    children.append(
                        ChildChunk(
                            item_section=item_section,
                            parent_index=p_idx,
                            chunk_index=c_idx,
                            content=snippet,
                        )
                    )
                    c_idx += 1
                if pos + child_max_chars >= len(p_text):
                    break
                pos += step

    return parents, children
