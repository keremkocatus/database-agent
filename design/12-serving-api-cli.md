# 12 — Serving: API ve CLI

## Amaç

Kullanıcının sisteme soru sorması ve yönetmesi için giriş noktaları. Karar: **FastAPI REST + CLI + Worker**, üçü de **Clean Architecture delivery** halkasında, ortak `Container` + use-case'leri çağırır. (Web chat UI sonraya — önce sağlam çekirdek.)

## Mimari: delivery → application → domain (Outfit deseni)

```
   src/api        src/cli        src/worker          ← delivery (ince kabuk)
      │              │               │
      └──────────────┴───────┬───────┘
                             ▼
                  application (use_cases, agent, ports)     ← iş mantığı
                             ▼
                        domain (entities, services, kurallar)
                             ▲
                  infrastructure (ports'u implemente eder)  ← MSSQL, Postgres, LLM…
```
- Üç giriş noktası da **yalnızca** `Container`'dan aldıkları use-case'leri çağırır; davranış tutarlı, iş mantığı tek yerde (`application`).
- `Container` (Composition Root, `infrastructure/container.py`) tüm port→adapter wiring'ini yapar; `api/main.py` lifespan'da kurulur → `app.state.container`; CLI ve worker kendi başlangıçlarında aynı Container'ı kurar.
- Hiçbir router/komut doğrudan infrastructure import etmez (Outfit ilkesi).

## FastAPI REST yüzeyi

Router'lar `endpoints/v1/`'te, request/response `schemas/`'ta, use-case injection `dependencies.py`'de (`Depends()`):

```
POST /v1/ask
  body: { question, server?, database?, object_kind?, category?, session_id? }
  resp: { answer, sources:[{uid,alias,type,why}], trace_id, confidence, note }

POST /v1/search                   # agent'sız ham hybrid arama (08)
  body: { query, top_k?, server?, database?, object_kind?, types?, writes_table? }
  resp: { results:[{uid,alias,type,score,summary,why}], confidence, note }

GET  /v1/objects/{uid}            # meta + (opsiyonel) ham SQL
GET  /v1/objects/{uid}/dependencies   # calls/reads/writes/dependents (04)
GET  /v1/objects/{uid}/history    # changelog/.prev.sql (03)
GET  /v1/tables/{uid}             # tablo/view sözlüğü kaydı (05)
GET  /v1/scope                    # hangi server/db'ler var (+ pending/degraded, kapsam filtreli)
GET  /v1/catalog/{server}/{db}    # kod+veri taksonomisi + kategori özetleri (06)

POST /v1/admin/sync               # sync job(lar)ı kuyruğa (11)
GET  /v1/admin/jobs               # kuyruk/dead-letter durumu (11)
GET  /v1/admin/runs               # son run'lar + drift + pending DB (11)
GET  /healthz                     # DB + provider capability probe (09)
```

- **Streaming:** `/ask` `Accept: text/event-stream` → **tipli SSE olay akışı** (understanding/clarification/plan/tool_call/tool_result/token/sources/confirmation_request/done) — UI adım-adım render eder; protokol `18`'de. `POST /v1/feedback` onay/düzeltme kaydeder (`18`).
- **Oturum + bellek:** `session_id` ile çok-turlu sohbet; kalıcı oturum, pencere+rolling-summary+semantik bellek ve `/v1/sessions` uçları **`17`**'de. Agent state sorgu-bazlı kalır (`10`).
- **Observability:** her istek `trace_id` ile metrik/trace/log'a bağlı (`16`).
- **Auth:** API-key (header) zorunlu. Kullanıcı→izinli server/db **kapsam (scope)** ve **rol-bazlı per-user `deny`** zorunlu filtre olarak uygulanır (`14`). Anahtarlar `.env`/secrets'ta, log'da maskeli.
- **Çok-kullanıcı + kapasite sinyali (`20`):** Eşzamanlı `/ask` oturumları izole (`17`) ve paralel işlenir; sistem kapasiteye yaklaşınca **kilitlenmez, bilgilendirir** — istek kuyruğa alınırsa SSE `queued {position, est_wait_ms}`, kapasite tükenirse `503 + Retry-After` + tipli `capacity` olayı. Eşzamanlılık/kapasite havuzları `20`'de.
- **Süreç sürüm uyumu (`11`):** API ↔ Worker farklı sürümde olabilir; job `payload.version` + geriye-uyumlu migration (önce şema→worker→api); bilinmeyen sürüm dead-letter değil **beklet** (`held`).

## CLI yüzeyi (Typer)

```
# sorgu
db-agent ask "teklif süresini hesaplayan SP hangisi?" [--db KaskoDB] [--verbose]
db-agent search "teklif süre" [--kind code] [--type procedure] [--top 10]  # ham arama
db-agent show <uid|alias> [--sql]                             # meta / ham SQL
db-agent deps <uid|alias>                                     # read/write bağımlılıklar
db-agent table <uid|alias>                                    # tablo/view sözlüğü

# yönetim (11) — kuyruğa atar
db-agent discover --server kasko-sql
db-agent sync [--server X] [--database Y] [--inline]
db-agent reindex --server X [--scope all]
db-agent jobs [--state pending|failed|dead]
db-agent status
db-agent serve     # uvicorn (api/main.py)
db-agent worker    # kuyruk-tüketen worker (src/worker)
```

- `--verbose`: ReAct adımlarını (think/araç/gözlem) basar → şeffaflık + debug.
- `search` agent'sız hızlı bakış; `ask` tam agent. İkisi de aynı retrieval use-case'ini çağırır.

## Neden önce API+CLI, UI sonra?

- CLI: geliştirme ve sync yönetimi için en hızlı, en sağlam zemin.
- REST: ileride herhangi bir frontend (web chat, VS Code eklentisi, dahili portal) buna bağlanır.
- Web UI değer katar ama ekstra frontend bakımı; çekirdek oturmadan erken. REST hazır olduğu için UI **eklenmesi kolay** kalır (sözleşme net).

## Dağıtım

- **İki process (varsayılan, `01`):** `db-agent serve` (API + APScheduler enqueue) + `db-agent worker` (kuyruk tüketici, ağır iş/GPU). Ortak Postgres. Worker yatay ölçeklenir.
- **Tek makine:** ikisi yan yana; **ayrık makine:** worker GPU'lu node'da, API hafif node'da — aynı kod, config farkı.
- Paketleme: `pyproject.toml` konsol scripti `db-agent` (Typer); opsiyonel Docker imajı (Python + ODBC driver + model cache). Migration'lar `migrations/` SQL-runner ile uygulanır.
