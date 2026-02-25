from __future__ import annotations
import html
import httpx

def send_message(bot_token: str, chat_id: str, text: str, timeout_s: float = 20.0) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()

def esc(s: str) -> str:
    return html.escape(s or "", quote=False)
