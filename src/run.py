from __future__ import annotations

import os
import re
import yaml
from datetime import datetime
from typing import List, Tuple, Optional

from .config import load_config
from .ingest import parse_feed, FeedItem
from .extract import fetch_article_text
from .groq_ai import groq_extract
from .score import compute_score
from .storage import init_db, load_state, save_state, is_seen, mark_seen, insert_deal
from .telegram import send_message, esc
from .utils import utc_now_iso, clamp, normalize_url


def load_sources(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sources", [])


def fmt_amount(amount) -> Optional[str]:
    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    if amount >= 1_000_000_000:
        return f"${amount/1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount/1_000:.0f}K"
    return f"${amount:.0f}"


def join_bullets(items, max_n=4):
    items = [x.strip() for x in (items or []) if x and x.strip()]
    return items[:max_n]


def infer_year_from_url(url: str) -> Optional[int]:
    u = (url or "").strip()
    m = re.search(r"/(20\d{2})/", u)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m = re.search(r"(20\d{2})", u)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def item_year(it: FeedItem) -> Optional[int]:
    # 1) published_at ISO
    if it.published_at:
        try:
            return datetime.fromisoformat(it.published_at.replace("Z", "+00:00")).year
        except Exception:
            pass
    # 2) url hint
    return infer_year_from_url(it.url)


def pass_year_filter(it: FeedItem, min_year: int, strict: bool) -> bool:
    y = item_year(it)
    if y is None:
        return not strict
    return y >= min_year


def format_signal_ru(title: str, url: str, deal: dict, score: int) -> str:
    # desired: no "Источник", no "Сигналы", link as icon
    link_icon = "↗"
    country = deal.get("country") or "LATAM"
    company = (deal.get("company") or "").strip() or "—"
    stage = deal.get("stage") or "Unknown"
    bm = deal.get("business_model") or "Unknown"
    sector = (deal.get("sector") or "unknown").strip().lower()
    amount_str = fmt_amount(deal.get("amount_usd")) or "—"

    ru_one_line = (deal.get("ru_one_line") or "").strip()

    lines = []
    lines.append(f"📡 <b>Сделка / сигнал</b> | {esc(country)}")
    lines.append(f"<b>{esc(company)}</b>")
    lines.append(f"{esc(clamp(title, 220))}")

    lines.append(f"Раунд: {esc(stage)} | Модель: {esc(bm)} | Инвестиции: {esc(amount_str)}")
    lines.append(f"Отрасль: {esc(sector)} | Оценка потенциала: {score}/100")

    if ru_one_line:
        lines.append(f"🧠 {esc(clamp(ru_one_line, 220))}")

    # link icon only
    safe_url = esc(normalize_url(url))
    lines.append(f'🔗 <a href="{safe_url}">{link_icon}</a>')

    return "\n".join(lines)


def format_note_ru(deal: dict, score: int, reasons: List[str]) -> str:
    why = join_bullets(deal.get("ru_why_important"), 4)
    плюсы = join_bullets(deal.get("ru_deal_angles"), 4)  # теперь это "плюсы проекта"
    watch = join_bullets(deal.get("ru_watchouts"), 3)

    lines = []
    lines.append(f"📝 <b>Короткая аналитика</b> (Оценка потенциала {score}/100)")
    if reasons:
        lines.append(f"⚙️ Скоринг: {esc(', '.join(reasons[:8]))}")

    if why:
        lines.append("\n<b>Почему важно</b>")
        for b in why:
            lines.append(f"• {esc(clamp(b, 160))}")

    if плюсы:
        lines.append("\n<b>Плюсы проекта</b>")
        for b in плюсы:
            lines.append(f"• {esc(clamp(b, 160))}")

    if watch:
        lines.append("\n<b>Риски / оговорки</b>")
        for b in watch:
            lines.append(f"• {esc(clamp(b, 160))}")

    return "\n".join(lines)


def main():
    cfg = load_config()
    init_db(cfg.db_path)
    state = load_state(cfg.state_path)

    sources = load_sources(cfg.sources_path)
    if not sources:
        raise RuntimeError("No sources found in sources.yaml")

    max_posts = int(os.getenv("MAX_POSTS_PER_RUN", "2"))
    min_year = int(os.getenv("MIN_PUBLISHED_YEAR", "2026"))
    strict = os.getenv("YEAR_FILTER_STRICT", "1") == "1"

    posted = 0
    processed = 0

    for s in sources:
        name = s["name"]
        feed_url = s["url"]

        items = parse_feed(name, feed_url)

        for it in items:
            if posted >= max_posts:
                break

            if not pass_year_filter(it, min_year=min_year, strict=strict):
                continue

            if is_seen(state, it.url, it.guid):
                continue

            processed += 1

            # 1) extract text
            try:
                text = fetch_article_text(it.url)
            except Exception:
                text = ""

            # 2) AI extract + RU analytics
            try:
                deal_obj = groq_extract(
                    api_key=cfg.groq_api_key,
                    model=cfg.groq_model,
                    title=it.title,
                    url=it.url,
                    source=it.source,
                    text=text,
                    fallback_summary=it.summary,
                )
            except Exception as e:
                print(f"[GROQ ERROR] {it.url} | {e}")
                # НЕ mark_seen — пусть попробует снова
                continue

            deal = deal_obj.model_dump()

            # 3) scoring
            sr = compute_score(
                country=deal.get("country"),
                stage=deal.get("stage"),
                sector=deal.get("sector"),
                business_model=deal.get("business_model"),
                signals=deal.get("signals") or [],
                investors=deal.get("investors") or [],
            )

            # 4) Post #1 (signal)
            signal_text = format_signal_ru(it.title, it.url, deal, sr.score)
            try:
                msg_id = send_message(cfg.telegram_bot_token, cfg.telegram_channel_id, signal_text)
            except Exception as e:
                print(f"[TG ERROR] {it.url} | {e}")
                continue

            # 5) Post #2 (note) as reply
            note_text = format_note_ru(deal, sr.score, sr.reasons)
            try:
                send_message(
                    cfg.telegram_bot_token,
                    cfg.telegram_channel_id,
                    note_text,
                    reply_to_message_id=msg_id,
                )
            except Exception:
                pass

            # 6) store
            record = {
                "created_at_utc": utc_now_iso(),
                "source": it.source,
                "title": it.title,
                "url": normalize_url(it.url),
                "guid": it.guid,
                "published_at": it.published_at,
                "company": deal.get("company"),
                "country": deal.get("country"),
                "stage": deal.get("stage"),
                "amount_usd": deal.get("amount_usd"),
                "investors": ",".join(deal.get("investors") or []),
                "sector": deal.get("sector"),
                "business_model": deal.get("business_model"),
                "signals": ",".join(deal.get("signals") or []),
                "one_line": deal.get("ru_one_line") or "",
                "confidence": deal.get("confidence"),
                "deal_score": sr.score,
                "score_reasons": ",".join(sr.reasons),
            }
            insert_deal(cfg.db_path, record)

            # 7) mark seen
            mark_seen(state, it.url, it.guid)
            posted += 1

        if posted >= max_posts:
            break

    save_state(cfg.state_path, state)
    print(f"Processed new items: {processed}, Posted deals: {posted}")


if __name__ == "__main__":
    main()
