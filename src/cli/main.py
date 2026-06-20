"""db-agent CLI (Typer) — M0-M2 deterministik çekirdek komutları (design/12, /19).

Inline sync (job-queue/worker M7'de). Tüm komutlar tek Container kurar; async use-case'ler
asyncio.run ile koşar.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

# Windows konsol/pipe cp1252 → Türkçe karakterlerde UnicodeEncodeError. UTF-8'e sabitle.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

import typer

from src.infrastructure.container import Container
from src.infrastructure.settings.config import Settings, load_servers_config

app = typer.Typer(add_completion=False, help="MSSQL agentic katalog — deterministik çekirdek (M0-M2)")

_ROOT = Path(__file__).resolve().parents[2]


def _container() -> Container:
    return Container(Settings())


def _run(coro):
    return asyncio.run(coro)


# --- M0: kurulum -----------------------------------------------------------
@app.command()
def init() -> None:
    """.env + config iskeleti üret, migration'ları uygula, pgvector/pg_trgm kur (design/19)."""

    for example, target in ((".env.example", ".env"), ("config/servers.example.yaml", "config/servers.yaml")):
        src, dst = _ROOT / example, _ROOT / target
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            typer.echo(f"  oluşturuldu: {target}")
        elif dst.exists():
            typer.echo(f"  zaten var:   {target}")

    async def _apply() -> None:
        c = _container()
        try:
            await c.db.execute_script(
                "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
            )
            result = await c.migrations.run()
            applied = ", ".join(result.applied) or "(yok — güncel)"
            typer.echo(f"  migration uygulanan: {applied}")
            typer.echo(f"  şema sürümü: {result.current_version}")
        finally:
            await c.aclose()

    _run(_apply())
    typer.secho("init tamam.", fg=typer.colors.GREEN)


@app.command()
def doctor() -> None:
    """Ön-uçuş kontrolü: config + Postgres + extension + migration + kaynak bağlantı (design/19)."""

    ok = True

    # 1) config
    try:
        cfg = load_servers_config(Settings().config_path)
        typer.secho(f"  [yeşil] config geçerli — {len(cfg.servers)} sunucu", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"  [kırmızı] config: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)

    async def _checks() -> bool:
        nonlocal ok
        c = _container()
        try:
            if await c.db.ping():
                ver = await c.migrations.current_version()
                typer.secho(f"  [yeşil] Postgres bağlı — şema sürümü: {ver}", fg=typer.colors.GREEN)
                ext = await c.db.fetch_all(
                    "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm')"
                )
                names = {e["extname"] for e in ext}
                missing = {"vector", "pg_trgm"} - names
                if missing:
                    ok = False
                    typer.secho(f"  [sarı] eksik extension: {missing} — 'db-agent init' çalıştır", fg=typer.colors.YELLOW)
                else:
                    typer.secho("  [yeşil] extension: vector + pg_trgm", fg=typer.colors.GREEN)
            else:
                ok = False
                typer.secho("  [kırmızı] Postgres'e bağlanılamadı (DATABASE_URL?)", fg=typer.colors.RED)

            for srv in cfg.servers:
                try:
                    src = c.source_for(srv.id)
                    probe_ok, msg = src.probe(srv.id)
                    color = typer.colors.GREEN if probe_ok else typer.colors.RED
                    tag = "yeşil" if probe_ok else "kırmızı"
                    if not probe_ok:
                        ok = False
                    typer.secho(f"  [{tag}] kaynak '{srv.id}': {msg[:80]}", fg=color)
                except Exception as exc:
                    ok = False
                    typer.secho(f"  [kırmızı] kaynak '{srv.id}': {exc}", fg=typer.colors.RED)

            # provider/GPU probları M3'te dolar
            typer.secho("  [stub] provider/GPU probu — M3'te etkinleşir", fg=typer.colors.BLUE)
            return ok
        finally:
            await c.aclose()

    healthy = _run(_checks())
    if healthy:
        typer.secho("doctor: tüm kontroller yeşil.", fg=typer.colors.GREEN)
    else:
        typer.secho("doctor: dikkat gereken kontroller var (yukarı bak).", fg=typer.colors.YELLOW)
        raise typer.Exit(1)


# --- M1/M2: indeksleme -----------------------------------------------------
@app.command()
def discover(server: str = typer.Option(..., "--server", "-s")) -> None:
    """Sunucudaki DB'leri keşfet (envanter listesi)."""

    async def _do() -> None:
        c = _container()
        try:
            src = c.source_for(server)
            cfg = c.servers_config.server(server)
            dbs = list(cfg.databases) if isinstance(cfg.databases, list) else src.discover_databases(server)
            typer.echo(f"sunucu '{server}' DB'leri: {', '.join(dbs)}")
            for db in dbs:
                inv = src.inventory_objects(server, db)
                by_type: dict[str, int] = {}
                for it in inv:
                    by_type[it.type] = by_type.get(it.type, 0) + 1
                typer.echo(f"  {db}: {json.dumps(by_type, ensure_ascii=False)}")
        finally:
            await c.aclose()

    _run(_do())


@app.command()
def sync(
    server: str = typer.Option(..., "--server", "-s"),
    database: str | None = typer.Option(None, "--database", "-d"),
    inline: bool = typer.Option(True, "--inline/--queue", help="M0-M2: yalnızca inline destekli"),
) -> None:
    """Keşif→extract→parse→tablo sözlüğü→Postgres (inline pipeline, design/01)."""

    if not inline:
        typer.secho("--queue M7'de (job-queue+worker). Şimdilik --inline kullan.", fg=typer.colors.YELLOW)
        raise typer.Exit(2)

    async def _do() -> None:
        c = _container()
        try:
            cfg = c.servers_config.server(server)
            if database:
                dbs = [database]
            elif isinstance(cfg.databases, list):
                dbs = list(cfg.databases)
            else:
                dbs = c.source_for(server).discover_databases(server)

            uc = c.sync_use_case(server)
            for db in dbs:
                typer.echo(f"sync {server}/{db} ...")
                summary = await uc.execute(server, db)
                typer.secho(
                    f"  [{summary.status}] {json.dumps(summary.counts, ensure_ascii=False)}",
                    fg=typer.colors.GREEN if summary.status == "ok" else typer.colors.RED,
                )
        finally:
            await c.aclose()

    _run(_do())


# --- M2: sorgu -------------------------------------------------------------
@app.command()
def show(target: str = typer.Argument(...), sql: bool = typer.Option(False, "--sql")) -> None:
    """Nesne meta'sı (+ opsiyonel ham SQL)."""

    async def _do() -> None:
        c = _container()
        try:
            result = await c.show_use_case().execute(target, with_sql=sql)
            if result is None:
                typer.secho(f"bulunamadı: {target}", fg=typer.colors.RED)
                raise typer.Exit(1)
            row = dict(result.object)
            row.pop("meta", None)
            typer.echo(json.dumps(_jsonable(row), ensure_ascii=False, indent=2, default=str))
            if sql and result.sql:
                typer.echo("\n--- SQL ---\n" + result.sql)
        finally:
            await c.aclose()

    _run(_do())


@app.command()
def deps(
    target: str = typer.Argument(...),
    incoming: bool = typer.Option(False, "--in", help="bağımlılar (kim kullanıyor)"),
    depth: int = typer.Option(6, "--depth"),
) -> None:
    """Bağımlılıklar (calls/reads/writes) veya bağımlılar (--in)."""

    async def _do() -> None:
        c = _container()
        try:
            res = await c.deps_use_case().execute(
                target, direction="in" if incoming else "out", max_depth=depth
            )
            if res is None:
                typer.secho(f"bulunamadı: {target}", fg=typer.colors.RED)
                raise typer.Exit(1)
            typer.echo(json.dumps(_jsonable(res), ensure_ascii=False, indent=2, default=str))
        finally:
            await c.aclose()

    _run(_do())


@app.command()
def table(target: str = typer.Argument(...)) -> None:
    """Tablo/view sözlüğü kaydı (kolon/PK/FK/check + okuyan/yazan nesneler)."""

    async def _do() -> None:
        c = _container()
        try:
            row = await c.table_use_case().execute(target)
            if row is None:
                typer.secho(f"tablo bulunamadı: {target}", fg=typer.colors.RED)
                raise typer.Exit(1)
            typer.echo(json.dumps(_jsonable(dict(row)), ensure_ascii=False, indent=2, default=str))
        finally:
            await c.aclose()

    _run(_do())


@app.command()
def status() -> None:
    """Katalog özeti + son run'lar."""

    async def _do() -> None:
        c = _container()
        try:
            res = await c.status_use_case().execute()
            typer.echo(json.dumps(_jsonable(res), ensure_ascii=False, indent=2, default=str))
        finally:
            await c.aclose()

    _run(_do())


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8000)) -> None:
    """Minimal API (M0: healthz). Tam REST yüzeyi M6."""
    import uvicorn

    uvicorn.run("src.api.main:app", host=host, port=port)


def _jsonable(value):
    """JSONB string alanlarını parse et (psycopg/asyncpg dict döndürür; güvenli geç)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


if __name__ == "__main__":
    app()
