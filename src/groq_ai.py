from __future__ import annotations
import json
import httpx
from pydantic import BaseModel, Field
from typing import List, Optional

class DealExtract(BaseModel):
    company: Optional[str] = None
    country: Optional[str] = None
    stage: Optional[str] = None          # Pre-Seed/Seed/Series A/...
    amount_usd: Optional[float] = None
    investors: List[str] = Field(default_factory=list)
    sector: Optional[str] = None         # fintech/edtech/etc
    business_model: Optional[str] = None # B2B/B2C/B2B2C/Unknown
    signals: List[str] = Field(default_factory=list)

    # RU output
    ru_one_line: Optional[str] = None            # 1 строка: что случилось
    ru_why_important: List[str] = Field(default_factory=list)  # 2-4 буллета
    ru_deal_angles: List[str] = Field(default_factory=list)    # 2-4 буллета (как зайти в сделку)
    ru_watchouts: List[str] = Field(default_factory=list)      # 0-3 риска/оговорки

    confidence: Optional[float] = None   # 0..1

SYSTEM = """You are a LATAM venture deals analyst.
Extract structured investment/deal facts from a news article and write brief analytics in Russian.

Return ONLY valid JSON matching the provided schema. No markdown. No extra keys.
If unknown, set null (or empty list).

Rules:
- country: single country if possible.
- stage must be one of: Pre-Seed, Seed, Series A, Series B, Series C, Growth, Debt, Grant, M&A, IPO, Unknown.
- business_model must be one of: B2B, B2C, B2B2C, Unknown.
- signals: choose from: expansion, enterprise, govtech, ai, fintech_infra, payments, hrtech, edtech, climate, logistics, cybersecurity, marketplace, devtools, data.

RU blocks style:
- ru_one_line: <= 160 chars.
- ru_why_important: 2-4 bullets, each <= 140 chars.
- ru_deal_angles: 2-4 bullets, each <= 140 chars. Practical: who to pitch / what offer / what entry point.
- ru_watchouts: 0-3 bullets, each <= 140 chars.
"""

def _extract_json_loose(text: str) -> dict:
    """
    Fallback: пытаемся вытащить первый JSON-объект из текста.
    """
    import re, json
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in model output")
    return json.loads(m.group(0))

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
            "text": content[:12000],
        },
        "instructions": "Return ONLY JSON. No markdown. No extra keys.",
    }

    base_payload = {
        "model": model,
        "temperature": 0.2,
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
        # Attempt #1: JSON mode (если поддерживается)
        payload = dict(base_payload)
        payload["response_format"] = {"type": "json_object"}
        r = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)

        # Если модель/аккаунт не поддерживает response_format — пробуем без него
        if r.status_code >= 400:
            err_txt = r.text
            payload2 = dict(base_payload)
            r2 = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload2)
            r2.raise_for_status()
            data = r2.json()
            raw = data["choices"][0]["message"]["content"]
            obj = _extract_json_loose(raw)
            return DealExtract.model_validate(obj)

        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]

    # Даже в JSON mode иногда прилетает мусор — подстрахуемся
    try:
        obj = json.loads(raw)
    except Exception:
        obj = _extract_json_loose(raw)

    return DealExtract.model_validate(obj)
