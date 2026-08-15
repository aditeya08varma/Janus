"""Deterministic year extraction — does not rely on the LLM."""

import re
from typing import List

from config import DEFAULT_YEAR, MIN_REG_YEAR

YEAR_RE = re.compile(r"\b(202[2-9]|20[3-9]\d)\b")


def extract_years(text: str, max_year: int = DEFAULT_YEAR) -> List[int]:
    """Return unique regulation years mentioned in text, in first-seen order."""
    seen = set()
    years: List[int] = []
    for match in YEAR_RE.findall(text or ""):
        year = int(match)
        if year < MIN_REG_YEAR or year > max_year + 1:
            continue
        if year not in seen:
            seen.add(year)
            years.append(year)
    return years


def resolve_search_years(query: str, target_year: int = DEFAULT_YEAR) -> List[int]:
    """
    Build the Pinecone year list.

    Mentioned years win over the LLM-inferred target_year so a comparison
    like "2024 vs 2026" actually searches both, not just [2026, 2025].
    A single-year query still includes a one-year lookback; cascade may
    widen further at retrieval time.
    """
    mentioned = extract_years(query)
    if mentioned:
        years = list(mentioned)
        if len(years) == 1 and years[0] > MIN_REG_YEAR:
            years.append(years[0] - 1)
        return sorted(set(years), reverse=True)

    year = target_year or DEFAULT_YEAR
    if year > MIN_REG_YEAR:
        return [year, year - 1]
    return [year]


def year_hint(text: str) -> str:
    years = extract_years(text)
    if not years:
        return ""
    return (
        f"\n\n[YEAR HINT: The query mentions {years}. "
        "Use these as target_year values. For comparisons, search each year.]"
    )
