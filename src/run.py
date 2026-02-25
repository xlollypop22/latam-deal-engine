from __future__ import annotations
import os
import yaml
from datetime import datetime, timezone
from .config import load_config
from .ingest import parse_feed
from .extract import fetch_article_text
from .groq_ai import groq_extract
from .score import compute_score
from .storage import init_db, load_state, save_state, is_seen, mark_seen, insert_deal
from .telegram import send_message, esc
from .utils import utc_now_iso, clamp

def load_sources(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])

def format_post(source: str, title: str, url: str, deal: dict, score: int) -> str:
    country = deal.get("country") or "LATAM"
    company = deal.get("company") or "Unknown"
    stage = deal.get("stage") or "Unknown"
    amount = deal.get("amount_usd")
    sector = deal.get("sector") or "unknown"
    bm = deal.get("business_model") or "Unknown"
    investors = deal.get("investors") or []
    signals = deal.get("signals") or []
    one_line = deal.get("one_line") or ""

    amount_str = ""
    if isinstance(amount, (int, float)) and amount > 0:
        # округлим красиво
        if amount >= 1_000_000:
            amount_str = f"${amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            amount_str = f"${amount/1_000:.0f}K"
        else:
            amount_str = f"${amount:.0f}"

    inv_str = ", ".join(investors[:5])
    sig_str = ", ".join(signals[:6])

    lines = []
    lines.append(f"🌎 <b>{esc(country)}</b> | <b>{esc(company)}</b>")
    head = f"{esc(title)}"
    lines.append(f"<b>{head}</b>")
    meta = f"Stage: <b>{esc(stage)}</b>"
    if amount_str:
        meta += f" | Amount: <b>{esc(amount_str)}</b>"
    meta += f" | Model: <b>{esc(bm)}</b>"
    lines.append(meta)

    lines.append(f"Sector: <b>{esc(sector)}</b> | Score: <b>{score}</b>/100")

    if one_line:
        lines.append(f"🧠 {esc(clamp(one_line, 220))}")

    if inv_str:
        lines.append(f"💼 Investors: {esc(inv_str)}")
    if sig_str:
        lines.append(f"🏷 Signals: {esc(sig_str)}")

    lines.append(f"🔗 {esc(url)}")
    lines.append(f"🗞 Source: {esc(source)}")

    return "\n".join(lines)

def main():
    cfg = load_config()
    init_db(cfg.db_path)
    state = load_state(cfg.state_path)

    sources = load_sources(cfg.sources_path)
    if not sources:
        raise RuntimeError("No sources found in sources.yaml")

    posted = 0
    processed = 0

    for s in sources:
        name = s["name"]
        feed_url = s["url"]
        items = parse_feed(name, feed_url)

        for it in items:
            if is_seen(state, it.url, it.guid):
                continue

            processed += 1

            # 1) extract article text
            text = ""
            try:
                text = fetch_article_text(it.url)
            except Exception:
                text = ""

            # 2) AI extraction
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
            except Exception:
                # если AI упал — всё равно сохраним seen, чтобы не долбить одну и ту же
                mark_seen(state, it.url, it.guid)
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

            # 4) publish
            post_text = format_post(it.source, it.title, it.url, deal, sr.score)
            try:
                send_message(cfg.telegram_bot_token, cfg.telegram_channel_id, post_text)
                posted += 1
            except Exception:
                # не отметим seen, чтобы попробовать ещё раз на следующем запуске
                continue

            # 5) store
            record = {
                "created_at_utc": utc_now_iso(),
                "source": it.source,
                "title": it.title,
                "url": it.url,
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
                "one_line": deal.get("one_line"),
                "confidence": deal.get("confidence"),
                "deal_score": sr.score,
                "score_reasons": ",".join(sr.reasons),
            }
            insert_deal(cfg.db_path, record)

            # 6) mark seen
            mark_seen(state, it.url, it.guid)

            # safety limit per run (чтобы не заспамить канал при первом старте)
            if posted >= 12:
                break

        if posted >= 12:
            break

    save_state(cfg.state_path, state)
    print(f"Processed new items: {processed}, Posted: {posted}")

if __name__ == "__main__":
    main()
