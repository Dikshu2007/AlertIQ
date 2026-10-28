from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AlertIQ"
    environment: str = "development"

    # --- Core ---
    database_url: str = "sqlite:///./alertiq.db"
    secret_key: str = "change-me-in-production"
    encryption_key: str = ""  # Fernet key; derived from secret_key when empty
    cors_origins: str = "http://localhost:5173,http://localhost:4173,capacitor://localhost,http://localhost,https://localhost"

    # --- Demo mode: seeds clearly-labeled sample data into an empty DB ---
    demo_mode: bool = False

    # --- Auth ---
    access_token_expire_minutes: int = 60 * 24

    # --- LLM provider (never exposed to the frontend) ---
    # provider: none | openai | anthropic | gemini
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""  # override for OpenAI-compatible endpoints
    llm_timeout_seconds: int = 45

    # --- Web research (for unfamiliar alerts) ---
    # provider: none | tavily | serper
    search_provider: str = "none"
    search_api_key: str = ""

    # --- Analysis tuning ---
    correlation_window_minutes: int = 30
    anomaly_iforest_min_samples: int = 50
    anomaly_scan_history_days: int = 14
    rate_limit_per_minute: int = 60


settings = Settings()
