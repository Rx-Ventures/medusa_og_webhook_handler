from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Medusa x Solidgate Payment Orchestrator"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    
    DATABASE_URL: str
    DB_POOL_SIZE: int = 3
    DB_MAX_OVERFLOW: int = 2
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 300
    DB_ECHO: bool = False
    
    SOLIDGATE_PUBLIC_KEY: str
    SOLIDGATE_SECRET_KEY: str
    SOLIDGATE_API_URL: str = "https://pay.solidgate.com/api/v1"
    SOLIDGATE_SUCCESS_URL: str = "https://merchant.example/success"
    SOLIDGATE_FAIL_URL: str = "https://merchant.example/fail"
    
    SECRET_KEY: str = "change-this-in-production-use-secrets-generate-32"
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"
    CORS_ORIGINS: str = "*"
    CORS_CREDENTIALS: bool = True
    
    REDIS_URL: str 
    REDIS_PASSWORD: str 

    MEDUSA_BASE_URL: str = "http://localhost:9000"
    MEDUSA_ADMIN_EMAIL: str 
    MEDUSA_ADMIN_PASSWORD: str
    MEDUSA_TOKEN_CACHE_TTL: int = 82800
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.lower() == "development"
    
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")
    
    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if "asyncpg" not in v:
            raise ValueError("DATABASE_URL must use asyncpg driver")
        return v
    
    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = ["development", "staging", "production"]
        if v.lower() not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of: {allowed}")
        return v.lower()

settings = Settings()