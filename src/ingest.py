from __future__ import annotations
import feedparser
from dataclasses import dataclass
from typing import List, Optional
from dateutil import parser as dtparser
from .utils import normalize_url

@dataclass
class FeedItem:
    source: str
    title: str
    url: str
    guid: str
    published_at: Optional[str]  # ISO
    summary: str

def parse_feed(source_name: str, feed_url: str) -> List[FeedItem]:
    d = feedparser.parse(feed_url)
    out: List[FeedItem] = []
    for e in d.entries[:50]:
        title = (getattr(e, "title", "") or "").strip()
        link = normalize_url(getattr(e, "link", "") or "")
        guid = (getattr(e, "id", "") or getattr(e, "guid", "") or link or title).strip()

        published_iso = None
        # published / updated / etc.
        for k in ("published", "updated", "created"):
            v = getattr(e, k, None)
            if v:
                try:
                    published_iso = dtparser.parse(v).astimezone(tz=None).isoformat()
                except Exception:
                    published_iso = None
                break

        summary = (getattr(e, "summary", "") or "").strip()
        if not link:
            continue

        out.append(
            FeedItem(
                source=source_name,
                title=title,
                url=link,
                guid=guid,
                published_at=published_iso,
                summary=summary,
            )
        )
    return out
