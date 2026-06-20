"""SQL-dosya migration runner (design/01, /13) — yoyo-tarzı, idempotent.

migrations/NNNN_*.sql dosyalarını sırayla uygular; uygulananları schema_migrations'ta izler.
ORM yok. Her dosya kendi içinde idempotent olacak şekilde yazılır (IF NOT EXISTS).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.infrastructure.persistence.database_client import DatabaseClient

_TRACK_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass
class MigrationResult:
    applied: list[str]
    skipped: list[str]
    current_version: str | None


class MigrationRunner:
    def __init__(self, db: DatabaseClient, migrations_dir: Path) -> None:
        self._db = db
        self._dir = migrations_dir

    def _files(self) -> list[Path]:
        return sorted(self._dir.glob("*.sql"))

    async def applied_versions(self) -> set[str]:
        await self._db.execute_script(_TRACK_TABLE)
        rows = await self._db.fetch_all("SELECT version FROM schema_migrations")
        return {r["version"] for r in rows}

    async def run(self) -> MigrationResult:
        applied_before = await self.applied_versions()
        applied: list[str] = []
        skipped: list[str] = []

        for path in self._files():
            version = path.stem  # ör. "0002_catalog"
            if version in applied_before:
                skipped.append(version)
                continue
            sql = path.read_text(encoding="utf-8")
            await self._db.execute_script(sql)
            await self._db.execute(
                "INSERT INTO schema_migrations (version) VALUES (:v) ON CONFLICT DO NOTHING",
                {"v": version},
            )
            applied.append(version)

        all_versions = sorted(applied_before | set(applied))
        current = all_versions[-1] if all_versions else None
        return MigrationResult(applied=applied, skipped=skipped, current_version=current)

    async def current_version(self) -> str | None:
        row = await self._db.fetch_one(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )
        return row["version"] if row else None
