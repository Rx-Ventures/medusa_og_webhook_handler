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
    MEDUSA_PUBLISHABLE_KEY: str

    # ── NetValve payment gateway settings ──
    # Required
    NETVALVE_API_KEY: str = ""
    # Optional credentials & identifiers
    NETVALVE_CLIENT_ID: str = ""
    NETVALVE_SITE_ID: str = ""
    NETVALVE_MID_ID_EUR: str = ""
    NETVALVE_MID_ID_USD: str = ""
    NETVALVE_MID_ID_PHP: str = ""
    # Environment: "sandbox" or "production"
    NETVALVE_ENVIRONMENT: str = "production"
    # URL overrides
    NETVALVE_BASE_URL: str = ""
    NETVALVE_SANDBOX_BASE_URL: str = ""
    NETVALVE_PRODUCTION_BASE_URL: str = ""
    NETVALVE_BACKOFFICE_API_URL: str = ""
    NETVALVE_PAYMENT_API_URL: str = ""
    # HPP settings
    NETVALVE_HPP_BASE_URL: str = ""
    NETVALVE_SANDBOX_HPP_BASE_URL: str = ""
    NETVALVE_PRODUCTION_HPP_BASE_URL: str = ""
    NETVALVE_HPP_DIRECT_URL: str = ""
    NETVALVE_HPP_FALLBACK_ENABLED: str = ""
    NETVALVE_HPP_ORDER_HOST: str = ""
    NETVALVE_HPP_ORDER_PATH: str = ""
    NETVALVE_HPP_MODE: str = ""
    NETVALVE_HPP_SUCCESS_URL: str = ""
    NETVALVE_HPP_CANCEL_URL: str = ""
    NETVALVE_HPP_FAILED_URL: str = ""
    NETVALVE_HPP_PENDING_URL: str = ""
    NETVALVE_RETURN_BASE_URL: str = ""
    # HPF script overrides
    NETVALVE_HPF_SCRIPT_SRC: str = ""
    NETVALVE_HPF_SCRIPT_INTEGRITY: str = ""
    NETVALVE_HPF_SCRIPT_FALLBACK_SRC: str = ""
    # Backoffice credentials
    NETVALVE_BASIC_AUTH_USERNAME: str = ""
    NETVALVE_BASIC_AUTH_PASSWORD: str = ""

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