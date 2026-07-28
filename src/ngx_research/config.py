from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EquityKobo"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/equitykobo"
    upload_dir: str = "./data/uploads"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    ngxpulse_api_key: str | None = None
    ngxpulse_base_url: str = "https://www.ngxpulse.ng"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
