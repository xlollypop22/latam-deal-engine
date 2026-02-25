import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    groq_api_key: str
    groq_model: str
    telegram_bot_token: str
    telegram_channel_id: str

    sources_path: str = "sources.yaml"
    state_path: str = "data/state.json"
    db_path: str = "data/deals.sqlite"

def load_config() -> Config:
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    # Дефолтная модель в Groq (можешь заменить в секретах GROQ_MODEL)
    groq_model = os.getenv("GROQ_MODEL", "").strip() or "llama-3.1-70b-versatile"

    missing = []
    if not groq_api_key: missing.append("GROQ_API_KEY")
    if not telegram_bot_token: missing.append("TELEGRAM_BOT_TOKEN")
    if not telegram_channel_id: missing.append("TELEGRAM_CHANNEL_ID")
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    return Config(
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        telegram_bot_token=telegram_bot_token,
        telegram_channel_id=telegram_channel_id,
    )
