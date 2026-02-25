from __future__ import annotations
import httpx
import trafilatura

def fetch_article_text(url: str, timeout_s: float = 20.0) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (DealEngineBot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout_s, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        html = r.text

    downloaded = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    return (downloaded or "").strip()
