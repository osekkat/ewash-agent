"""Environment configuration loaded via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Meta Cloud API
    meta_app_secret: str
    meta_verify_token: str
    meta_access_token: str
    meta_phone_number_id: str
    meta_waba_id: str = ""

    # Server
    port: int = 8000
    log_level: str = "INFO"


settings = Settings()
