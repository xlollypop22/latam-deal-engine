from __future__ import annotations
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional
from .utils import utc_now_iso, normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at_utc TEXT NOT NULL,
  source TEXT,
  title TEXT,
  url TEXT UNIQUE,
  guid TEXT,
  published_at TEXT,
  company TEXT,
  country TEXT,
  stage TEXT,
  amount_usd REAL,
  investors TEXT,
  sector TEXT,
  business_model TEXT,
  signals TEXT,
  one_line TEXT,
  confidence REAL,
  deal_score INTEGER,
  score_reasons TEXT
);
"""

def ensure_dirs(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def init_db(db_path: str) -> None:
    ensure_dirs(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(SCHEMA)
        con.commit()
    finally:
        con.close()

def load_state(state_path: str) -> Dict[str, Any]:
    ensure_dirs(state_path)
    if not os.path.exists(state_path):
        return {"seen_urls": [], "seen_guids": [], "last_run_utc": None}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state_path: str, state: Dict[str, Any]) -> None:
    ensure_dirs(state_path)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_seen(state: Dict[str, Any], url: str, guid: str) -> bool:
    u = normalize_url(url)
    return u in set(state.get("seen_urls", [])) or (guid and guid in set(state.get("seen_guids", [])))

def mark_seen(state: Dict[str, Any], url: str, guid: str) -> None:
    u = normalize_url(url)
    seen_urls = set(state.get("seen_urls", []))
    seen_guids = set(state.get("seen_guids", []))
    seen_urls.add(u)
    if guid:
        seen_guids.add(guid)

    # ограничим рост
    state["seen_urls"] = list(seen_urls)[-5000:]
    state["seen_guids"] = list(seen_guids)[-5000:]
    state["last_run_utc"] = utc_now_iso()

def insert_deal(
    db_path: str,
    record: Dict[str, Any],
) -> None:
    con = sqlite3.connect(db_path)
    try:
        cols = ",".join(record.keys())
        placeholders = ",".join(["?"] * len(record))
        sql = f"INSERT OR IGNORE INTO deals ({cols}) VALUES ({placeholders})"
        con.execute(sql, list(record.values()))
        con.commit()
    finally:
        con.close()
