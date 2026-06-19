# 13 — Tech Stack ve Yol Haritası

## Tech stack özeti

| Katman | Araç | Neden |
|---|---|---|
| Dil | Python 3.11+ | Ekosistem (DB, ML, web) tek dilde |
| MSSQL erişimi | `pyodbc` + ODBC Driver 18 | Salt-okunur keşif/extraction (`02`,`03`) |
| SQL parsing | `sqlglot` (tsql) | AST, bağımlılık, parametre çıkarımı (`04`) |
| Bağımlılık (server) | `sys.dm_sql_referenced_entities` | En doğru bağımlılık kaynağı (`04`) |
| LLM erişimi | İnce custom adapter | Provider-agnostik, minimum bağımlılık (`09`) |
| LLM (lokal) | vLLM / Ollama | Taban **Qwen2.5-14B-AWQ** (tek 24GB), `hardware_profile: auto` ile yukarı/aşağı ölçeklenir; GPU zorunlu değil (cloud/CPU) (`09`) |
| LLM (cloud) | Vertex / OpenAI / Anthropic | "Hangisi varsa onunla" (`09`) |
| Embedding | BGE-M3 (varsayılan, swappable) | Çokdilli + kod, lokal (`07`) |
| Reranker | bge-reranker-v2-m3 | Cross-encoder isabet (`08`) |
| Veri katmanı | PostgreSQL + pgvector (+pg_trgm) | Dense+sparse+metadata+graph tek DB (`07`) |
| DB erişimi | SQLAlchemy **async engine** + ham `text()` SQL | Outfit `DatabaseClient` deseni; ORM yok, **DB-tarafı RPC/function yok** |
| Driver | asyncpg (engine altında) | `postgresql+asyncpg://` |
| Migration | SQL-dosya + runner (yoyo tarzı) | ORM'siz, hafif, versiyonlu (`01`) |
| Lexical | BGE-M3 sparse + pg_trgm | Öğrenilmiş lexical + fuzzy ad (`08`) |
| Orchestration | Custom ReAct loop | Tam kontrol, framework yok (`10`) |
| Scheduler/Queue | APScheduler + Postgres job-queue | Scheduler enqueue, worker tüketir (`01`,`11`) |
| API | FastAPI + uvicorn | REST + streaming (`12`) |
| CLI | Typer | Sorgu + sync yönetimi (`12`) |
| Mimari | Clean Architecture (api/application/domain/infrastructure) | Outfit ile tutarlı; AI = application/agent slice; **koda yeni şey eklemek = yeni adapter/use-case** (port arkası) |
| Güvenlik | Exclusion + per-user görünürlük + injection guardrail + scope auth | Kritik gizlilik (`14`); redaction YOK → `allow_cloud` |
| Eşzamanlılık/kapasite | Havuz + kuyruk + perf-test | Çok-kullanıcı, kilitlenmez, ölçülebilir (`20`) |
| Observability | Prometheus + Grafana + OpenTelemetry + JSON log + token ölçümü | Takip edilebilirlik + kaynak kök-neden (`16`/`20`) |
| Chat | Oturum + pencere/özet/semantik bellek (Postgres+pgvector) | Chatbot (`17`) |
| Config | Pydantic Settings + YAML + `.env` | Çok-sunucu + secret + exclusion (`02`,`14`) |
| Paketleme | `pyproject.toml` (+ ops. Docker) | `db-agent` konsol scripti |

## Bağımlılıklar (çekirdek)
`pyodbc`, `sqlglot`, **`sqlalchemy[asyncio]` + `asyncpg`** + `pgvector`, `sentence-transformers`/`FlagEmbedding` (BGE-M3 + reranker), `httpx` (provider çağrıları), `fastapi`+`uvicorn`, `typer`, `apscheduler`, `pyyaml`, `pydantic`/`pydantic-settings`, `yoyo-migrations`, **`prometheus-client` + `opentelemetry-sdk`** (`16`). Bilinçli olarak **yok:** LangChain/LlamaIndex/LiteLLM/CrewAI, ORM modelleri, DB-tarafı stored procedure/RPC, ayrı vektör/graph DB.

## Repo iskeleti (Clean Architecture — Outfit deseni)

```
database-agent/
├── config/servers.example.yaml
├── .env.example
├── data/                                # lokal store (gitignore) — git YOK (03)
├── migrations/                          # numaralı .sql dosyaları + runner (01)
├── design/                             # bu tasarım dokümanları
├── src/
│   ├── api/                            # HTTP delivery (12)
│   │   ├── main.py                     # lifespan, middleware, Container→app.state
│   │   ├── dependencies.py             # Depends() → use case injection
│   │   ├── endpoints/v1/               # ask, search, objects, tables, catalog, admin, health
│   │   ├── schemas/                    # request/response (Pydantic)
│   │   └── middlewares/                # error, logging, rate-limit, prometheus
│   ├── cli/                            # Typer delivery (12) — ask/search/sync/worker/…
│   ├── worker/                         # kuyruk-tüketen delivery (11)
│   │
│   ├── application/                    # use case + ports (framework-light)
│   │   ├── ports/                      # Protocol'ler: llm, embedding, reranker,
│   │   │                               #   source_db, catalog_repo, vector_repo,
│   │   │                               #   graph_repo, object_store, job_queue
│   │   ├── use_cases/
│   │   │   ├── discovery/  extraction/  parsing/  dictionary/
│   │   │   ├── enrich/  categorize/  indexing/  retrieval/  sync/
│   │   │   └── chat/                   # oturum + memory + summarize (17)
│   │   ├── agent/                      # ReAct orchestration + tool registry (10)
│   │   ├── mappers/                    # entity → DTO
│   │   ├── dtos/                       # katmanlar arası
│   │   └── cache_keys.py
│   │
│   ├── domain/                        # saf iş kuralları (framework bağımsız)
│   │   ├── entities/                   # CatalogObject, TableDef, DependencyEdge,
│   │   │                               #   Taxonomy, Category, SearchResult, AgentTrace
│   │   ├── services/                   # agent_prompts, ranking/RRF kuralları, policy
│   │   ├── exceptions/
│   │   └── value_objects/              # Uid, Alias, Hash, Score…
│   │
│   └── infrastructure/                # port implementasyonları
│       ├── container.py                # Composition Root: DI wiring + lifecycle
│       ├── source/mssql/               # pyodbc connector + keşif (02/03)
│       ├── persistence/
│       │   ├── database_client.py      # SQLAlchemy async engine + text() (RPC YOK)
│       │   ├── repositories/           # catalog, vector, graph, jobs, runs, chat (ham SQL)
│       │   └── object_store/           # disk store + manifest + changelog (03)
│       ├── llm/                        # vllm, ollama, vertex, openai, anthropic (09)
│       ├── embedding/bge_m3.py         # dense+sparse (07)
│       ├── rerank/bge_reranker.py      # cross-encoder (08)
│       ├── parsing/sqlglot_parser.py   # (04)
│       ├── scheduling/apscheduler.py   # enqueue (11)
│       ├── observability/              # metrics, tracing, JSON log (16)
│       └── settings/config.py          # Pydantic Settings (.env + YAML + exclusions 14)
├── tests/
└── pyproject.toml
```

**Bağımlılık kuralı:** `api/cli/worker → application → domain`; `infrastructure` yalnızca `application/ports`'u implemente eder. Hiçbir use case infrastructure'ı doğrudan import etmez (Outfit ilkesi). Agent (10) bir application use-case'idir; tool'ları diğer use-case'leri/port'ları çağırır; prompt'lar `domain/services`'te; LLM/embedding/reranker `infrastructure` adapter'ları `ports` arkasında (`09`).

## Yol haritası (build sırası)

Bağımlılık sırasına göre, her milestone tek başına test edilebilir:

**M0 — İskelet:** Clean Architecture iskeleti (api/application/domain/infrastructure + cli/worker), `Container` (Composition Root), Pydantic Settings + YAML config, `DatabaseClient` (SQLAlchemy async engine), Postgres + pgvector + pg_trgm, SQL-dosya migration runner, `healthz`, **`init`/`doctor` + docker-compose (`19`)**.

**M1 — Keşif + Extraction (LLM'siz):** `connector` + `extractor` + `store`. `db-agent discover` ve `db-agent sync` ham SQL'i + manifest'i indirir. Değişim tespiti (modify_date+hash) çalışır. → *Çıktı: lokal store dolu.*

**M2 — Parsing + Tablo sözlüğü (LLM'siz):** `parser` + `dictionary`. meta.json + tablo JSON + bağımlılık kenarları. → *Çıktı: yapısal metadata + graph.*

**M3 — Provider katmanı:** `llm` adapter; en az bir lokal (vLLM/Ollama) + bir cloud provider; `embed` (BGE-M3). Smoke test: chat + embed. → *Çıktı: LLM/embedding hazır.*

**M4 — Enrich + Categorize + Index:** özet/açıklama, DB-başına taksonomi, foldering (README+catalog), object card embed → Postgres. → *Çıktı: aranabilir indeks.*

**M5 — Retrieval:** hybrid + RRF + reranker; `db-agent search` ve `/search`. Altın set ile recall@k/MRR ölç. → *Çıktı: ham arama çalışıyor + ölçülü.*

**M6 — Agent + Chat + Etkileşim:** ReAct loop + tools; `db-agent ask` ve `/ask`; oturum + bellek (`17`); netleştirme + onay/feedback döngüsü + tipli SSE streaming (`18`). → *Çıktı: çok-turlu, responsive, adım-adım izlenebilir soru-cevap.*

**M7 — Scheduling + Serving:** Postgres job-queue + `worker` entrypoint, APScheduler enqueue, reconciler, `/admin/*`, `serve`, streaming. → *Çıktı: kendini güncelleyen servis.*

**M8 — Sertleştirme:** resume/lock, hata state'leri + **fail-listesi (`09`)**, **observability + token ölçümü (`16`)**, **güvenlik (`14`: exclusion + per-user görünürlük + injection + scope auth; redaction YOK → `allow_cloud`/provider seçimi)**, **eşzamanlılık/kapasite + perf-test (`20`)**, **test/eval (`15`)**, **yedek/restore + DR (`19`: disk + config + Postgres-otoriter; ham SQL disk-only riski)**, retention, decommission, webhook, Docker/compose, dokümantasyon. (Opsiyonel: web chat UI, şifreli secrets store, veri profilleme.)

## Açık kaynak notları
- LICENSE repoda mevcut. README + bu `design/` seti açık kaynak için yeterli başlangıç dokümantasyonu.
- `data/`, `.env`, model cache `.gitignore`'da.
- Hassas örnek config yerine `servers.example.yaml` + `.env.example`.

## Ölçek notu (karar — v1 sade tutulur)
Bu sürüm **büyük-ölçek özel çözümü kurmaz** (sharding / ayrı vektör DB / partition). Yaklaşım:
DB-başına izole keşif + arama (`10`), DB-by-DB bootstrap (`19`), ve **sürekli kaynak takibi**
(`16`/`20`) ile yük kök-nedeninin erken görülmesi. Korpus çok büyürse bu sinyallere dayanarak
ileride ölçek çözümü eklenir — şimdiden değil. (Ayrıntı: `REVIEW-gap-analysis` 1.5.)

## Çözülmeyi bekleyen / çözülen açık sorular
1. ~~Windows Authentication~~ → SQL auth seçildi (`02`).
2. Bir sunucuda kaç DB / toplam kaç nesne? → **Ölçek-bağımsız tasarım** (yukarıdaki not); kesin
   sayı kapasite ayarı (`20` `concurrency.yaml`) için `db-agent perf-test` ile ölçülür. Yine de
   gerçek beklenti girilirse havuz varsayılanları daha iyi ayarlanır.
3. Lokal GPU spesifikasyonu → **Çözüldü:** GPU zorunlu değil (cloud/CPU de çalışır). **Taban profili tek 24 GB**
   (Qwen2.5-14B-AWQ + BGE-M3 + reranker); `hardware_profile: auto` doctor'ın algıladığı donanıma göre
   yukarı ölçeklenir (48GB/2-GPU → daha yüksek hassasiyet + daha çok slot), aşağı düşer (CPU → 7B fallback). `09`.
4. Cloud-izinli vs sadece-lokal DB ayrımı → `allow_cloud` kapsam-bazlı (`09`/`14`); hassas DB = lokal-zorunlu. Operatör kararı.
5. Altın set kim sağlayacak? → M5'te **sentetik tohum (LLM ile nesne→soru üret) + ~30 elle-onaylı çekirdek**
   (sahibi proje sahibi/alan uzmanı), sonra feedback ile büyür (`15`). Sentetik üretim cold-start'ı çözer.
6. Serving erişim modeli → API-key + scope + **rol-bazlı per-user `deny`** (`14` §3.1) seçildi; SSO/OIDC ileride.
7. ~~Redaction politikası~~ → **Redaction yok** (karar). Hassas içerik için sorumluluk kullanıcının provider seçiminde (`allow_cloud`, `14`).
8. Gerçek kapasite (kaç eşzamanlı kullanıcı) → **Sabit sayı yok; kapasiteden ölçeklenir.** `auto` havuzlar
   (doctor algılar) + `db-agent perf-test` ölçer (doygunluğun ~%70-80'i hedef); donanım büyüyünce kapasite
   kendiliğinden artar, dolunca `capacity` sinyali (`20`). Net rakam = perf-test çıktısı.
