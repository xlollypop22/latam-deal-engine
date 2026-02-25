from __future__ import annotations
import json
import httpx
from pydantic import BaseModel, Field
from typing import List, Optional

class DealExtract(BaseModel):
    company: Optional[str] = None
    country: Optional[str] = None
    stage: Optional[str] = None          # Seed / Series A / etc
    amount_usd: Optional[float] = None   # если удалось
    investors: List[str] = Field(default_factory=list)
    sector: Optional[str] = None         # fintech / edtech / etc
    business_model: Optional[str] = None # B2B / B2C / B2B2C / unknown
    signals: List[str] = Field(default_factory=list)  # expansion, enterprise, govtech, ai, etc
    one_line: Optional[str] = None       # 1 строка, смысл
    confidence: Optional[float] = None   # 0..1

SYSTEM = """You are a LATAM venture deals analyst.
Extract structured investment/deal facts from a news article.
Return ONLY valid JSON matching the provided schema. No markdown. No extra keys.
If a field is unknown, set it to null (or empty list).
Prefer LATAM country names when possible.
Stage must be one of: Pre-Seed, Seed, Series A, Series B, Series C, Growth, Debt, Grant, M&A, IPO, Unknown.
Business model must be one of: B2B, B2C, B2B2C, Unknown.
Signals examples: expansion, enterprise, govtech, ai, fintech_infra, payments, hrtech, edtech, climate, logistics, cybersecurity.
"""

def groq_extract(
    api_key: str,
    model: str,
    title: str,
    url: str,
    source: str,
    text: str,
    fallback_summary: str,
    timeout_s: float = 30.0,
) -> DealExtract:
    content = text.strip() if text.strip() else fallback_summary.strip()

    user_prompt = {
        "schema": DealExtract.model_json_schema(),
        "input": {
            "source": source,
            "title": title,
            "url": url,
            "text": content[:12000],  # ограничим
        },
        "instructions": "Extract best-effort. amount_usd as number if possible. country as a single country.",
    }

    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout_s) as client:
        r = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data["choices"][0]["message"]["content"]
    obj = json.loads(raw)
    return DealExtract.model_validate(obj)
