from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EquityKobo"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/equitykobo"
    upload_dir: str = "./data/uploads"
    cors_origins: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:5174,"
        "http://127.0.0.1:5174"
    )
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.5"
    openai_reasoning_effort: str = "medium"
    openai_pdf_detail: str = "low"
    openai_pdf_max_pages: int = 35
    openai_pdf_selection_max_chars: int = 90000
    ngxpulse_api_key: str | None = None
    ngxpulse_base_url: str = "https://www.ngxpulse.ng"
    database_pool_size: int = 15
    database_max_overflow: int = 30
    database_pool_timeout_seconds: int = 10
    database_pool_recycle_seconds: int = 1800
    automation_enabled: bool = True
    automation_run_on_startup: bool = True
    automation_interval_minutes: int = 1440
    automation_scheduled_sync_mode: str = "daily"
    automation_dividend_sync_enabled: bool = True
    automation_daily_dividend_sync_enabled: bool = False
    ngxpulse_request_pause_seconds: float = 3.2

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
