from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://expense:expense@localhost:15439/expense_tracker"
    redis_url: str = "redis://localhost:16379/0"
    jwt_secret: str = "replace-this-with-a-long-random-secret"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    login_failure_limit: int = 5
    login_failure_window_seconds: int = 900
    app_env: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
