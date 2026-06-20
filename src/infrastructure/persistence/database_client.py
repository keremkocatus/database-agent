"""DatabaseClient — SQLAlchemy async engine + ham text() SQL (design/13).

Outfit deseni: ORM yok, DB-tarafı RPC/function yok. Repository'ler bunun üstüne ham SQL ile biner.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


class DatabaseClient:
    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url, echo=echo, pool_pre_ping=True
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]

    async def fetch_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            row = result.mappings().first()
            return dict(row) if row else None

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(sql), params or {})

    async def execute_script(self, sql_script: str) -> None:
        """AUTOCOMMIT ile çok-deyimli script (migration / CREATE EXTENSION)."""
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(sql_script)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Per-object upsert için tek transaction (design/01)."""
        async with self._engine.begin() as conn:
            yield conn

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self._engine.dispose()


async def exec_many(conn: AsyncConnection, sql: str, rows: Sequence[dict[str, Any]]) -> None:
    if rows:
        await conn.execute(text(sql), rows)
