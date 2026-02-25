from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class ScoreResult:
    score: int
    reasons: List[str]

FOCUS_COUNTRIES = {"argentina", "brazil", "brasil", "mexico", "colombia", "chile", "peru", "uruguay"}
FOCUS_SECTORS = {"edtech", "hrtech", "fintech", "fintech_infra", "payments", "ai", "cybersecurity", "logistics", "govtech"}

def compute_score(
    country: Optional[str],
    stage: Optional[str],
    sector: Optional[str],
    business_model: Optional[str],
    signals: List[str],
    investors: List[str],
) -> ScoreResult:
    s = 0
    reasons: List[str] = []

    c = (country or "").strip().lower()
    st = (stage or "Unknown").strip()
    sec = (sector or "").strip().lower()
    bm = (business_model or "Unknown").strip()

    if c and c.lower() in FOCUS_COUNTRIES:
        s += 12; reasons.append("focus_country")

    if bm == "B2B":
        s += 18; reasons.append("b2b")
    elif bm == "B2B2C":
        s += 10; reasons.append("b2b2c")

    stage_points = {
        "Seed": 10,
        "Series A": 22,
        "Series B": 18,
        "Series C": 14,
        "Growth": 12,
        "Debt": 6,
        "Grant": 6,
        "M&A": 16,
        "IPO": 20,
        "Pre-Seed": 8,
        "Unknown": 0
    }
    s += stage_points.get(st, 0)
    if stage_points.get(st, 0) > 0:
        reasons.append(f"stage_{st}")

    # sector / signals
    sigset = {x.strip().lower() for x in (signals or []) if x}
    if sec in FOCUS_SECTORS:
        s += 12; reasons.append("focus_sector")

    if "expansion" in sigset:
        s += 14; reasons.append("expansion_signal")
    if "enterprise" in sigset:
        s += 10; reasons.append("enterprise_signal")
    if "govtech" in sigset:
        s += 8; reasons.append("govtech_signal")

    # simple investor signal: if many named investors
    named_investors = [x for x in (investors or []) if x and len(x.strip()) >= 3]
    if len(named_investors) >= 3:
        s += 6; reasons.append("multiple_investors")
    elif len(named_investors) == 2:
        s += 3; reasons.append("some_investors")

    # clamp
    s = max(0, min(100, s))
    return ScoreResult(score=s, reasons=reasons)
