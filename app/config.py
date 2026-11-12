from functools import cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    # env_prefix sprawia, że Pydantic szuka zmiennych z przedrostkiem POSTGRES_
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="POSTGRES_", extra="ignore"
    )

    user: str  # Automatycznie szuka POSTGRES_USER
    password: SecretStr  # Automatycznie szuka POSTGRES_PASSWORD
    host: str = "localhost"  # Automatycznie szuka POSTGRES_HOST
    port: int = Field(default=5432, ge=1, le=65535)  # Automatycznie szuka POSTGRES_PORT
    db: str  # Automatycznie szuka POSTGRES_DB

    def to_psycopg_connection_kwargs(self) -> dict[str, str | int]:
        return {
            "dbname": self.db,
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "password": self.password.get_secret_value(),
        }


@cache
def get_database_settings() -> DatabaseSettings:

    return DatabaseSettings()
