"""Catalyst evidence-quality grading, ported from tw-stock-ai's
src/news/catalyst_confidence.py (Taiwan Chinese terms → English/US newswire
terms). The grade describes evidence quality only — it does NOT imply
buy/sell advice, and is not folded into total_score (display/transparency
metadata, same as the TW original)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalystConfidence:
    grade: str
    label: str
    reason: str
    evidence_count: int


CONFIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}

CONFIRMED_TERMS = (
    "announced", "announces", "confirmed", "official", "filed", "files",
    "8-k", "10-k", "10-q", "s-1", "sec filing", "prospectus",
    "earnings", "q1 results", "q2 results", "q3 results", "q4 results",
    "revenue of", "signed", "definitive agreement", "fda approval",
    "granted approval", "shareholder meeting", "guidance raised",
)

REPORT_TERMS = (
    "reuters", "bloomberg", "cnbc", "wall street journal", "wsj",
    "barron's", "marketwatch", "techcrunch", "axios", "financial times",
    "the information", "nikkei",
)

RUMOR_TERMS = (
    "reportedly", "sources say", "sources familiar", "is said to",
    "in talks", "considering", "could", "may", "eyes", "targets",
    "expected to", "weighing",
)

SPECULATIVE_TERMS = (
    "could revolutionize", "future of", "long-term vision", "someday",
    "eventually", "moonshot", "blue sky", "game-changer", "disruptive",
    "next big thing", "paradigm shift",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def classify_catalyst_confidence(headlines: list[str]) -> CatalystConfidence:
    """Classify event credibility from matched headlines."""
    evidence = [h for h in headlines if h]
    if not evidence:
        return CatalystConfidence("D", "低", "未找到可佐證新聞", 0)

    joined = " ".join(evidence[:5])
    confirmed_hits = sum(1 for h in evidence if _contains_any(h, CONFIRMED_TERMS))
    report_hits = sum(1 for h in evidence if _contains_any(h, REPORT_TERMS))
    rumor_hits = sum(1 for h in evidence if _contains_any(h, RUMOR_TERMS))
    speculative_hits = sum(1 for h in evidence if _contains_any(h, SPECULATIVE_TERMS))

    if confirmed_hits >= 2 or (confirmed_hits >= 1 and len(evidence) >= 2 and rumor_hits == 0):
        return CatalystConfidence("A", "已確認", "公告/財報/正式事件佐證", len(evidence))
    if report_hits >= 1 and rumor_hits <= report_hits:
        return CatalystConfidence("B", "高可信報導", "可信媒體或多源報導，仍需追蹤正式文件", len(evidence))
    if rumor_hits >= 1:
        return CatalystConfidence("C", "市場傳聞", "含傳聞或 sources say 類訊號，需等正式確認", len(evidence))
    if speculative_hits >= 1 or _contains_any(joined, SPECULATIVE_TERMS):
        return CatalystConfidence("D", "概念延伸", "偏概念或長線想像，短線需降低權重", len(evidence))
    if len(evidence) >= 3:
        return CatalystConfidence("B", "多源升溫", "多則新聞同時命中，可信度中高", len(evidence))
    return CatalystConfidence("C", "一般新聞", "新聞命中但缺少正式佐證", len(evidence))
