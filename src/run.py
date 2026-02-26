from __future__ import annotations

import os
import re
from datetime import datetime, timezone
import yaml

from .config import load_config
from .ingest import parse_feed
from .extract import fetch_article_text
from .groq_ai import groq_extract
from .score import compute_score
from .storage import (
    init_db,
    load_state,
    save_state,
    is_seen,
    mark_seen,
    insert_deal,
)
from .telegram import send_message, esc
from .utils import utc_now_iso, clamp, normalize_url


def load_sources(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sources", [])


# ----------------- Filters -----------------

def parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Python 3.11: fromisoformat понимает "+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def extract_year_from_url(url: str) -> int | None:
    # TechCrunch: /2025/03/27/...
    m = re.search(r"/(20\d{2})/", url or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def is_allowed_year(published_at_iso: str | None, url: str, min_year: int) -> bool:
    dt = parse_iso_dt(published_at_iso)
    if dt:
        return dt.year >= min_year
    y = extract_year_from_url(url)
    if y is not None:
        return y >= min_year
    # если год определить нельзя — считаем НЕ ок, когда strict
    return False


def fmt_amount(amount):
    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount/1_000:.0f}K"
    return f"${amount:.0f}"


def join_bullets(items, max_n=4):
    items = [x.strip() for x in (items or []) if x and x.strip()]
    return items[:max_n]


# ----------------- Formatting (your desired style) -----------------

LINK_ICON = os.getenv("LINK_ICON", "↗").strip() or "↗"


def format_signal_ru(source: str, title: str, url: str, deal: dict, score: int) -> str:
    country = deal.get("country") or "LATAM"
    company = deal.get("company") or "Компания"
    stage = deal.get("stage") or "Unknown"
    bm = deal.get("business_model") or "Unknown"
    sector = deal.get("sector") or "unknown"

    amount_str = fmt_amount(deal.get("amount_usd")) or "—"
    investors = deal.get("investors") or []
    ru_one_line = deal.get("ru_one_line") or ""

    inv_str = ", ".join(investors[:4])

    # ссылка в иконку (экономия места)
    link = f"{LINK_ICON} {esc(url)}"

    lines = []
    lines.append(f"📡 Сделка / сигнал | {esc(country)}")
    lines.append(f"Компания: {esc(company)}")
    lines.append(f"{esc(clamp(title, 220))}")

    lines.append(f"Раунд: {esc(stage)} | Инвестиции: {esc(amount_str)} | Модель: {esc(bm)}")
    lines.append(f"Сектор: {esc(sector)} | Оценка потенциала: {score}/100")

    if ru_one_line:
        lines.append(f"🧠 {esc(clamp(ru_one_line, 220))}")

    if inv_str:
        lines.append(f"💼 Инвесторы: {esc(inv_str)}")

    lines.append(link)

    # убрали "Источник" и "Сигналы" — как ты хотел
    return "\n".join(lines)


def format_note_ru(deal: dict, score: int, reasons: list[str]) -> str:
    why = join_bullets(deal.get("ru_why_important"), 4)

    # "Как зайти в сделку" → "Плюсы проекта"
    pros = join_bullets(deal.get("ru_deal_angles"), 4)

    watch = join_bullets(deal.get("ru_watchouts"), 3)

    lines = []
    lines.append(f"📝 Короткая аналитика (оценка {score}/100)")

    if reasons:
        lines.append(f"⚙️ Скоринг: {esc(', '.join(reasons[:8]))}")

    if why:
        lines.append("\nПочему проект важен")
        for b in why:
            lines.append(f"• {esc(clamp(b, 160))}")

    if pros:
        lines.append("\nПлюсы проекта")
        for b in pros:
            lines.append(f"• {esc(clamp(b, 160))}")

    if watch:
        lines.append("\nРиски / оговорки")
        for b in watch:
            lines.append(f"• {esc(clamp(b, 160))}")

    return "\n".join(lines)


# ----------------- MAIN -----------------

def main():
    cfg = load_config()
    init_db(cfg.db_path)

    state = load_state(cfg.state_path)
    sources = load_sources(cfg.sources_path)
    if not sources:
        raise RuntimeError("No sources found in sources.yaml")

    # env controls
    max_posts = int(os.getenv("MAX_POSTS_PER_RUN", "2"))
    min_year = int(os.getenv("MIN_PUBLISHED_YEAR", "2026"))
    strict_year = os.getenv("YEAR_FILTER_STRICT", "1") == "1"

    # “новое” считаем от last_run_utc
    last_run_dt = parse_iso_dt(state.get("last_run_utc"))
    if last_run_dt is None:
        # если первый запуск/нет даты — берём очень старую точку
        last_run_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)

    posted = 0
    processed_new = 0

    # debug counters
    skipped_seen = 0
    skipped_not_newer_than_last_run = 0
    skipped_year = 0

    for s in sources:
        name = s["name"]
        feed_url = s["url"]

        items = parse_feed(name, feed_url)

        for it in items:
            u = normalize_url(it.url)

            if is_seen(state, u, it.guid):
                skipped_seen += 1
                continue

            # фильтр “новее прошлого запуска”
            it_dt = parse_iso_dt(it.published_at)
            if it_dt and it_dt <= last_run_dt:
                skipped_not_newer_than_last_run += 1
                continue

            # фильтр по году (2026+)
            if strict_year and not is_allowed_year(it.published_at, u, min_year):
                skipped_year += 1
                continue

            processed_new += 1

            # 1) extract text
            try:
                text = fetch_article_text(u)
            except Exception:
                text = ""

            # 2) AI extract + RU analytics
            try:
                deal_obj = groq_extract(
                    api_key=cfg.groq_api_key,
                    model=cfg.groq_model,
                    title=it.title,
                    url=u,
                    source=it.source,
                    text=text,
                    fallback_summary=it.summary,
                )
            except Exception as e:
                print(f"[GROQ ERROR] {u} | {e}")
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
            signal_text = format_signal_ru(it.source, it.title, u, deal, sr.score)
            try:
                msg_id = send_message(cfg.telegram_bot_token, cfg.telegram_channel_id, signal_text)
            except Exception as e:
                print(f"[TG ERROR] {u} | {e}")
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
                "url": u,
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
                "one_line": deal.get("ru_one_line"),
                "confidence": deal.get("confidence"),
                "deal_score": sr.score,
                "score_reasons": ",".join(sr.reasons),
            }
            insert_deal(cfg.db_path, record)

            # 7) mark seen
            mark_seen(state, u, it.guid)
            posted += 1

            if posted >= max_posts:
                break

        if posted >= max_posts:
            break

    save_state(cfg.state_path, state)

    print(
        "Processed new items: "
        f"{processed_new}, Posted deals: {posted} | "
        f"skipped_seen={skipped_seen}, "
        f"skipped_old={skipped_not_newer_than_last_run}, "
        f"skipped_year={skipped_year}"
    )


if __name__ == "__main__":
    main()
