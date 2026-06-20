"""Config modeli — YAML (servers.yaml) + .env secrets (design/02, /14).

- Sunucu/DB listesi, kapsam, exclusion → YAML (git'e girebilir).
- Şifreler → .env / ortam değişkeni (git'e girmez).
- Postgres DSN + data dir → ortam değişkeni (app-level).
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Resilience(BaseModel):
    max_retries: int = 3
    backoff_seconds: float = 5.0


class Defaults(BaseModel):
    driver: str = "ODBC Driver 18 for SQL Server"
    auth: str = "sql"
    encrypt: bool = True
    trust_server_certificate: bool = True
    application_intent: str = "ReadOnly"
    app_name: str = "db-agent-catalog"
    schedule: str = "0 3 * * *"
    embedding_model: str = "bge-m3"
    resilience: Resilience = Field(default_factory=Resilience)
    new_database_policy: str = "discover_then_approve"


class ServerConfig(BaseModel):
    id: str
    host: str
    username_env: str
    password_env: str
    databases: str | list[str] = "auto"  # "auto" | [names]
    exclude_databases: list[str] = Field(default_factory=lambda: ["tempdb", "model", "msdb", "master"])
    approved_databases: list[str] = Field(default_factory=list)
    include_schemas: str | list[str] = "all"
    object_types: list[str] = Field(
        default_factory=lambda: ["procedure", "view", "function", "trigger", "table"]
    )
    exclude_object_patterns: list[str] = Field(default_factory=list)
    include_object_patterns: str | list[str] = "all"
    schedule: str | None = None

    def username(self) -> str:
        val = os.getenv(self.username_env)
        if not val:
            raise RuntimeError(f"Ortam değişkeni boş: {self.username_env} (.env kontrol et)")
        return val

    def password(self) -> str:
        val = os.getenv(self.password_env)
        if not val:
            raise RuntimeError(f"Ortam değişkeni boş: {self.password_env} (.env kontrol et)")
        return val


class ExclusionRule(BaseModel):
    """design/14 — eşleşen nesne tamamen görünmez (çekilmez/indekslenmez/aranmaz)."""

    model_config = ConfigDict(populate_by_name=True)

    server: str
    database: str | None = None
    # YAML anahtarı 'schema'; öznitelik 'schema_' (BaseModel.schema gölgelemesini önler).
    schema_: str | None = Field(default=None, alias="schema")
    types: list[str] | None = None
    names: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    reason: str | None = None

    def matches(self, *, server: str, database: str, schema: str, name: str, type_: str) -> bool:
        if self.server != server:
            return False
        if self.database and self.database != database:
            return False
        if self.schema_ and self.schema_ != schema:
            return False
        if self.types and type_ not in self.types:
            return False
        full = f"{schema}.{name}"
        if name in self.names or full in self.names:
            return True
        for pat in self.patterns:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(full, pat):
                return True
        # names/patterns boşsa kural tip/şema kapsamına göre eşleşir
        return not self.names and not self.patterns


class Settings(BaseSettings):
    """App-level ayarlar (.env / ortam). YAML config ayrıca load_servers_config ile gelir."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dbagent:dbagent@localhost:5432/dbagent"
    data_dir: Path = Path("data")
    config_path: Path = Path("config/servers.yaml")
    api_key: str | None = None


class ServersConfig(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    servers: list[ServerConfig] = Field(default_factory=list)
    exclusions: list[ExclusionRule] = Field(default_factory=list)

    def server(self, server_id: str) -> ServerConfig:
        for s in self.servers:
            if s.id == server_id:
                return s
        raise KeyError(f"Sunucu config'te yok: {server_id!r}")

    def is_excluded(self, *, server: str, database: str, schema: str, name: str, type_: str) -> bool:
        return any(
            rule.matches(server=server, database=database, schema=schema, name=name, type_=type_)
            for rule in self.exclusions
        )


def load_servers_config(path: Path) -> ServersConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Config bulunamadı: {path}. 'db-agent init' ile iskelet üret veya "
            f"config/servers.example.yaml'ı kopyala."
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ServersConfig(**data)
