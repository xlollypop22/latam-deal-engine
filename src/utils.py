import re
from datetime import datetime, timezone

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def normalize_url(url: str) -> str:
    u = (url or "").strip()
    # убираем UTM и хвосты
    u = re.sub(r"[?&]utm_[^=]+=[^&]+", "", u)
    u = re.sub(r"[?&]fbclid=[^&]+", "", u)
    u = re.sub(r"[?&]gclid=[^&]+", "", u)
    # чистим лишние ? или &
    u = re.sub(r"[?&]$", "", u)
    return u

def clamp(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"
