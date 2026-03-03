from dataclasses import dataclass

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Provider configuration dataclasses ──────────────────────────────────────
# These provide typed, grouped access to provider-specific settings via
# `settings.solidgate_config` and `settings.netvalve_config` properties.
# The underlying env vars remain flat (SOLIDGATE_*, NETVALVE_*) for
# backward compatibility.


@dataclass(frozen=True)
class SolidgateConfig:
    """Solidgate payment provider configuration."""

    public_key: str
    secret_key: str
    api_url: str
    success_url: str
    fail_url: str


@dataclass(frozen=True)
class NetvalveConfig:
    """NetValve payment provider configuration."""

    api_key: str
    client_id: str
    site_id: str
    mid_id_eur: str
    mid_id_usd: str
    mid_id_php: str
    environment: str
    base_url: str
    payment_api_url: str
    sandbox_base_url: str
    production_base_url: str
    backoffice_api_url: str
    # HPP settings
    hpp_base_url: str
    sandbox_hpp_base_url: str
    production_hpp_base_url: str
    hpp_direct_url: str
    hpp_fallback_enabled: str
    hpp_order_host: str
    hpp_order_path: str
    hpp_mode: str
    hpp_success_url: str
    hpp_cancel_url: str
    hpp_failed_url: str
    hpp_pending_url: str
    return_base_url: str
    # HPF script overrides
    hpf_script_src: str
    hpf_script_integrity: str
    hpf_script_fallback_src: str
    # Backoffice credentials
    basic_auth_username: str
    basic_auth_password: str


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

    # ── Solidgate settings ──
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

    # ── NetValve settings ──
    NETVALVE_API_KEY: str = ""
    NETVALVE_CLIENT_ID: str = ""
    NETVALVE_SITE_ID: str = ""
    NETVALVE_MID_ID_EUR: str = ""
    NETVALVE_MID_ID_USD: str = ""
    NETVALVE_MID_ID_PHP: str = ""
    NETVALVE_ENVIRONMENT: str = "production"
    NETVALVE_BASE_URL: str = ""
    NETVALVE_PAYMENT_API_URL: str = ""
    NETVALVE_SANDBOX_BASE_URL: str = ""
    NETVALVE_PRODUCTION_BASE_URL: str = ""
    NETVALVE_BACKOFFICE_API_URL: str = ""
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

    SOLIDGATE_RECURRING_IP: str = "203.0.113.0"

    SLACK_ALERTS_URL: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Typed provider config accessors ─────────────────────────────────

    @property
    def solidgate_config(self) -> SolidgateConfig:
        """Grouped access to all Solidgate settings."""
        return SolidgateConfig(
            public_key=self.SOLIDGATE_PUBLIC_KEY,
            secret_key=self.SOLIDGATE_SECRET_KEY,
            api_url=self.SOLIDGATE_API_URL,
            success_url=self.SOLIDGATE_SUCCESS_URL,
            fail_url=self.SOLIDGATE_FAIL_URL,
        )

    @property
    def netvalve_config(self) -> NetvalveConfig:
        """Grouped access to all NetValve settings."""
        return NetvalveConfig(
            api_key=self.NETVALVE_API_KEY,
            client_id=self.NETVALVE_CLIENT_ID,
            site_id=self.NETVALVE_SITE_ID,
            mid_id_eur=self.NETVALVE_MID_ID_EUR,
            mid_id_usd=self.NETVALVE_MID_ID_USD,
            mid_id_php=self.NETVALVE_MID_ID_PHP,
            environment=self.NETVALVE_ENVIRONMENT,
            base_url=self.NETVALVE_BASE_URL,
            payment_api_url=self.NETVALVE_PAYMENT_API_URL,
            sandbox_base_url=self.NETVALVE_SANDBOX_BASE_URL,
            production_base_url=self.NETVALVE_PRODUCTION_BASE_URL,
            backoffice_api_url=self.NETVALVE_BACKOFFICE_API_URL,
            hpp_base_url=self.NETVALVE_HPP_BASE_URL,
            sandbox_hpp_base_url=self.NETVALVE_SANDBOX_HPP_BASE_URL,
            production_hpp_base_url=self.NETVALVE_PRODUCTION_HPP_BASE_URL,
            hpp_direct_url=self.NETVALVE_HPP_DIRECT_URL,
            hpp_fallback_enabled=self.NETVALVE_HPP_FALLBACK_ENABLED,
            hpp_order_host=self.NETVALVE_HPP_ORDER_HOST,
            hpp_order_path=self.NETVALVE_HPP_ORDER_PATH,
            hpp_mode=self.NETVALVE_HPP_MODE,
            hpp_success_url=self.NETVALVE_HPP_SUCCESS_URL,
            hpp_cancel_url=self.NETVALVE_HPP_CANCEL_URL,
            hpp_failed_url=self.NETVALVE_HPP_FAILED_URL,
            hpp_pending_url=self.NETVALVE_HPP_PENDING_URL,
            return_base_url=self.NETVALVE_RETURN_BASE_URL,
            hpf_script_src=self.NETVALVE_HPF_SCRIPT_SRC,
            hpf_script_integrity=self.NETVALVE_HPF_SCRIPT_INTEGRITY,
            hpf_script_fallback_src=self.NETVALVE_HPF_SCRIPT_FALLBACK_SRC,
            basic_auth_username=self.NETVALVE_BASIC_AUTH_USERNAME,
            basic_auth_password=self.NETVALVE_BASIC_AUTH_PASSWORD,
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
