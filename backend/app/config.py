from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    oms_api_key: str = "dev-key"
    oms_database_url: str = "sqlite:///./data/oms.db"
    oms_sync_enabled: bool = True
    oms_sync_interval_seconds: float = 3
    oms_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    apca_api_key_id: str = ""
    apca_api_secret_key: str = ""
    apca_api_base_url: str = "https://paper-api.alpaca.markets"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
