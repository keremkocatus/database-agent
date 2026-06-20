"""Minimal FastAPI — M0: healthz (DB + migration sürümü probu). Tam REST yüzeyi M6 (design/12)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.infrastructure.container import Container


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.container = Container()
    yield
    await app.state.container.aclose()


app = FastAPI(title="db-agent", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    container: Container = app.state.container
    db_ok = await container.db.ping()
    version = await container.migrations.current_version() if db_ok else None
    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "database": "ok" if db_ok else "unreachable",
        "schema_version": version,
    }
