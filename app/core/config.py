from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "FinWise AI"
    env: str = "dev"
    secret_key: str = "change-me-in-prod"
    access_token_expire_minutes: int = 1440
    refresh_token_expire_days: int = 7
    database_url: str = "postgresql+psycopg2://finwise:finwise@localhost:5432/finwise"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_extract_model: str = "openai/gpt-oss-20b"
    pinecone_api_key: str = ""
    pinecone_index: str = "finwise-plans"
    cors_origins: str = "http://localhost:5173"
    upload_dir: str = "./uploads"
    max_upload_mb: int = 20


settings = Settings()
