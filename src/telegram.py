from __future__ import annotations

import html
import httpx
from typing import Optional


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    reply_to_message_id: Optional[int] = None,
    timeout_s: float = 20.0,
    disable_web_page_preview: bool = True,
) -> int:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True

    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return int(data["result"]["message_id"])
