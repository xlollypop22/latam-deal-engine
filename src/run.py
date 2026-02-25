from __future__ import annotations
import yaml
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

def format_signal_ru(source: str, title: str, url: str, deal: dict, score: int) -> str:
    country = deal.get("country") or "LATAM"
    company = deal.get("company") or "Компания"
    stage = deal.get("stage") or "Unknown"
    amount_str = fmt_amount(deal.get("amount_usd"))
    sector = deal.get("sector") or "unknown"
    bm = deal.get("business_model") or "Unknown"
    investors = deal.get("investors") or []
    sig = deal.get("signals") or []
    ru_one_line = deal.get("ru_one_line") or ""

    inv_str = ", ".join(investors[:4])
    sig_str = ", ".join(sig[:6])

    lines = []
    lines.append(f"📡 <b>Сделка / сигнал</b> | <b>{esc(country)}</b>")
    lines.append(f"<b>{esc(company)}</b>")
    lines.append(f"{esc(clamp(title, 220))}")

    meta = f"Раунд: <b>{esc(stage)}</b>"
    if amount_str:
        meta += f" | Сумма: <b>{esc(amount_str)}</b>"
    meta += f" | Модель: <b>{esc(bm)}</b>"
    lines.append(meta)

    lines.append(f"Сектор: <b>{esc(sector)}</b> | Deal Score: <b>{score}</b>/100")

    if ru_one_line:
        lines.append(f"🧠 {esc(clamp(ru_one_line, 220))}")

    if inv_str:
        lines.append(f"💼 Инвесторы: {esc(inv_str)}")
    if sig_str:
        lines.append(f"🏷 Сигналы: {esc(sig_str)}")

    lines.append(f"🔗 {esc(url)}")
    lines.append(f"🗞 Источник: {esc(source)}")

    return "\n".join(lines)

def format_note_ru(deal: dict, score: int, reasons: list[str]) -> str:
    why = join_bullets(deal.get("ru_why_important"), 4)
    angles = join_bullets(deal.get("ru_deal_angles"), 4)
    watch = join_bullets(deal.get("ru_watchouts"), 3)

    lines = []
    lines.append(f"📝 <b>Короткая аналитика</b> (Score {score}/100)")
    if reasons:
        lines.append(f"⚙️ Скоринг: {esc(', '.join(reasons[:8]))}")

    if why:
        lines.append("\n<b>Почему важно</b>")
        for b in why:
            lines.append(f"• {esc(clamp(b, 160))}")

    if angles:
        lines.append("\n<b>Как зайти в сделку</b>")
        for b in angles:
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
                # НЕ mark_seen — пусть попробует снова на следующем запуске
                continue

            deal = deal_obj.model_dump()

            # 3) scoring (rules)
            sr = compute_score(
                country=deal.get("country"),
                stage=deal.get("stage"),
                sector=deal.get("sector"),
                business_model=deal.get("business_model"),
                signals=deal.get("signals") or [],
                investors=deal.get("investors") or [],
            )

            # 4) Post #1 (signal)
            signal_text = format_signal_ru(it.source, it.title, it.url, deal, sr.score)
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
                # если нота не ушла — это не критично, продолжаем
                pass

            # 6) store
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
                "one_line": deal.get("ru_one_line") or deal.get("ru_one_line"),  # храним RU
                "confidence": deal.get("confidence"),
                "deal_score": sr.score,
                "score_reasons": ",".join(sr.reasons),
            }
            insert_deal(cfg.db_path, record)

            # 7) mark seen
            mark_seen(state, it.url, it.guid)

            posted += 1
            # лимит, чтобы не заспамить канал на первом запуске
            if posted >= 10:
                break

        if posted >= 10:
            break

    save_state(cfg.state_path, state)
    print(f"Processed new items: {processed}, Posted deals: {posted}")

if __name__ == "__main__":
    main()
