"""Parent-child chunking for CBP CROSS rulings.

Why parent-child, concretely
----------------------------
A CROSS ruling is a legal document with a fixed rhetorical shape: it describes
the merchandise, states the issue, reasons through competing headings, and then
*holds*. Almost all of the answer-bearing signal is in the last two sections,
and most of the token count is in the first two.

Embedding the whole ruling therefore dilutes exactly the text a classification
question is asking about — the holding paragraph gets averaged together with a
long product description that mentions materials, packaging and country of
origin. Retrieval then matches on incidental description rather than on the
legal reasoning.

So: **children are embedded, parents are returned.** Each child is one
reasoning or holding paragraph; the parent is the full ruling, which is what
the fast path hands to the LLM so the citation is a whole, quotable ruling
rather than a fragment torn out of context.

Section weighting is not decoration either. `HOLDING` states the operative
subheading, so a child drawn from it is scored above one drawn from a
background section during rollup — a ruling whose *holding* matches the query
is a better answer than one that merely mentions the product in passing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from marsa.ingestion.cross import extract_hts_codes
from marsa.ingestion.schemas import CrossRuling

# Section headers as they actually appear in CROSS rulings. Matched at line
# start, case-insensitive, tolerating a trailing colon and surrounding space.
SECTION_PATTERN = re.compile(
    r"^\s*(DESCRIPTION OF MERCHANDISE|FACTS|ISSUE|LAW AND ANALYSIS|ANALYSIS"
    r"|HOLDING|EFFECT ON OTHER RULINGS|MERCHANDISE)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Rollup weight per section. A holding is the decision; a description is
# context. Tuned so a holding match outranks a description match of the same
# raw similarity, without letting a weak holding beat a strong description.
SECTION_WEIGHTS: dict[str, float] = {
    "HOLDING": 1.00,
    "LAW AND ANALYSIS": 0.92,
    "ANALYSIS": 0.92,
    # The subject line states product and origin in the exact shape users
    # phrase lookups ("classification of X from Y"), so it is scored well above
    # background prose despite being metadata rather than reasoning.
    "SUBJECT": 0.80,
    "ISSUE": 0.75,
    "DESCRIPTION OF MERCHANDISE": 0.62,
    "MERCHANDISE": 0.62,
    "FACTS": 0.62,
    "EFFECT ON OTHER RULINGS": 0.50,
    "PREAMBLE": 0.55,
}
DEFAULT_WEIGHT = 0.6

# Word counts, not tokens: this runs before any tokenizer is chosen, and the
# ratio to real tokens is close enough at ~1.3x for these limits to hold.
MIN_CHUNK_WORDS = 25
MAX_CHUNK_WORDS = 220
CHUNK_OVERLAP_WORDS = 40

# The minimum length is there to suppress noise fragments ("We disagree."), not
# to discard short high-value sections. Applying it uniformly silently deletes
# every HOLDING from the index — and a holding is frequently two sentences:
#
#     "The applicable subheading will be 8507.60.0020. The rate of duty
#      will be free."
#
# That is 15 words and it is the single most important sentence in the ruling.
# These sections keep a floor low enough to always survive.
SHORT_SECTION_FLOOR = 4
ALWAYS_KEEP_SECTIONS = frozenset({"HOLDING", "ISSUE", "SUBJECT"})


def min_words_for(section: str) -> int:
    return (
        SHORT_SECTION_FLOOR
        if section.upper() in ALWAYS_KEEP_SECTIONS
        else MIN_CHUNK_WORDS
    )


@dataclass(frozen=True)
class Chunk:
    """A child chunk: the unit that gets embedded and BM25-indexed."""

    chunk_id: str
    parent_id: str
    ruling_number: str
    section: str
    ordinal: int
    text: str
    hts_codes: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def weight(self) -> float:
        return SECTION_WEIGHTS.get(self.section.upper(), DEFAULT_WEIGHT)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def split_sections(body: str) -> list[tuple[str, str]]:
    """Split ruling text into (section_name, section_text) pairs.

    Text before the first recognised header becomes `PREAMBLE` — real rulings
    open with addressee and tariff-number lines that carry useful signal and
    should not be silently dropped.
    """
    if not body or not body.strip():
        return []

    matches = list(SECTION_PATTERN.finditer(body))
    if not matches:
        return [("PREAMBLE", body.strip())]

    sections: list[tuple[str, str]] = []

    lead = body[: matches[0].start()].strip()
    if lead:
        sections.append(("PREAMBLE", lead))

    for i, match in enumerate(matches):
        name = match.group(1).strip().upper()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        if text:
            sections.append((name, text))

    return sections


def split_paragraphs(text: str) -> list[str]:
    """Blank-line paragraphs, falling back to single newlines.

    Some ruling bodies arrive with every line wrapped and no blank lines at
    all; treating that as one paragraph would produce a single oversized chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) == 1 and len(paragraphs[0].split()) > MAX_CHUNK_WORDS:
        paragraphs = [p.strip() for p in paragraphs[0].split("\n") if p.strip()]
    return paragraphs


def _pack(paragraphs: list[str]) -> list[str]:
    """Merge paragraphs up to MAX_CHUNK_WORDS; split anything still oversized.

    Merging matters because legal prose alternates long reasoning paragraphs
    with one-line transitions ("We disagree."). Left alone those become chunks
    whose embedding is noise.
    """
    packed: list[str] = []
    buffer: list[str] = []
    buffered_words = 0

    def flush() -> None:
        nonlocal buffer, buffered_words
        if buffer:
            packed.append("\n\n".join(buffer))
            buffer = []
            buffered_words = 0

    for paragraph in paragraphs:
        words = paragraph.split()

        if len(words) > MAX_CHUNK_WORDS:
            flush()
            # Sliding window with overlap so a sentence spanning the cut is not
            # lost to both halves.
            step = MAX_CHUNK_WORDS - CHUNK_OVERLAP_WORDS
            for start in range(0, len(words), step):
                window = words[start : start + MAX_CHUNK_WORDS]
                if len(window) >= MIN_CHUNK_WORDS or start == 0:
                    packed.append(" ".join(window))
                if start + MAX_CHUNK_WORDS >= len(words):
                    break
            continue

        if buffered_words + len(words) > MAX_CHUNK_WORDS:
            flush()
        buffer.append(paragraph)
        buffered_words += len(words)

    flush()
    return packed


def chunk_ruling(ruling: CrossRuling) -> list[Chunk]:
    """Produce the child chunks for one ruling.

    The subject line is always emitted as its own chunk: it is short, dense,
    and states the product and origin country in the form users actually
    phrase questions ("classification of X from Y"). It routinely outperforms
    the body on short lookup queries.
    """
    chunks: list[Chunk] = []
    ordinal = 0
    parent_id = ruling.doc_id

    def add(section: str, text: str, *, force: bool = False) -> None:
        nonlocal ordinal
        if not force and len(text.split()) < min_words_for(section):
            return
        chunks.append(
            Chunk(
                chunk_id=f"{parent_id}::{ordinal}",
                parent_id=parent_id,
                ruling_number=ruling.ruling_number,
                section=section,
                ordinal=ordinal,
                text=text,
                hts_codes=tuple(extract_hts_codes(text)),
                metadata={
                    "collection": ruling.collection,
                    "category": ruling.category or "",
                    "ruling_date": ruling.ruling_date.isoformat() if ruling.ruling_date else "",
                },
            )
        )
        ordinal += 1

    if ruling.subject:
        add("SUBJECT", ruling.subject.strip())

    for section, text in split_sections(ruling.body):
        for packed in _pack(split_paragraphs(text)):
            add(section, packed)

    # Last resort: a ruling whose body is too short to clear the minimum would
    # otherwise be represented by its subject line alone. Real CROSS entries do
    # this — revocation and modification notices are frequently one sentence.
    # `force` is required here: routing this through the normal filter is what
    # made the guard a no-op, since the filter is exactly what it exists to
    # bypass.
    if not any(c.section != "SUBJECT" for c in chunks) and ruling.body.strip():
        add("PREAMBLE", ruling.body.strip(), force=True)

    return chunks


def chunk_rulings(rulings: Iterator[CrossRuling]) -> Iterator[Chunk]:
    for ruling in rulings:
        yield from chunk_ruling(ruling)
