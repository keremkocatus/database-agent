# db-agent — MSSQL Agentic Katalog (Incomplete)

MSSQL sunucularını keşfeden, programlanabilir nesneleri (SP/View/Function/Trigger) ve tablo
şemalarını lokale indiren, yapısal metadata + bağımlılık grafiği üreten açık kaynak sistem.
Tasarım kararları [`design/`](design/README.md) altında.

Bu repo şu an **M0–M4** kapsamını içerir:

| Milestone | İçerik |
|---|---|
| **M0** | Clean Architecture iskeleti + `Container` + config + Postgres veri katmanı + migration runner + `init`/`doctor` + `healthz` + docker-compose |
| **M1** | Connector + Extractor + Store: `discover`/`sync` ile ham SQL + manifest indirme, `modify_date`+hash değişim tespiti |
| **M2** | Parser (sqlglot) + tablo sözlüğü: `meta.json` + tablo JSON + bağımlılık kenarları → disk **ve** Postgres (`objects`/`edges`) |
| **M3** | Provider katmanı (`design/09`): LLM (vLLM/Ollama/OpenAI/Anthropic/Vertex) + embedding (BGE-M3 lokal + cloud) + structured output + `allow_cloud` guard + önbellek |
| **M4** | Enrich (özet/açıklama + kalite kapısı) + taksonomi (seed+LLM) + categorize + card embed → Postgres `embeddings` + disk `catalog/` (README+catalog.json) |

Retrieval/agent/scheduling/serving (M5+) henüz **yok**; port arayüzleri sonraki milestone'lara hazır.

### Opsiyonel bağımlılıklar
- `pip install -e ".[local]"` — BGE-M3 lokal embedding (FlagEmbedding+torch, design/07 varsayılan).
- `pip install -e ".[vertex]"` — Vertex AI (Gemini) auth.
- Cloud chat/embedding (OpenAI/Anthropic) ekstra gerektirmez (httpx çekirdekte). `allow_cloud: true` + `.env` anahtarı.

## Mimari

`cli → application → domain`; `infrastructure` yalnızca `application/ports`'u implemente eder.
Wiring [`src/infrastructure/container.py`](src/infrastructure/container.py)'da. Detay: `design/13`.

## Kurulum

```bash
python -m venv .venv && . .venv/Scripts/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Çalıştırma (uçtan uca)

Postgres + seed/demo MSSQL gerektirir (docker-compose ile gelir):

```bash
docker compose up -d                 # postgres(pgvector) + seed mssql (DemoDB)
db-agent init                        # .env/config iskeleti + migration + extension
db-agent doctor                      # config + Postgres + extension + kaynak bağlantı kontrolü

db-agent sync --server demo --inline # discover→extract→parse→(enrich→categorize→embed)→Postgres
                                     #   (--no-llm: yapısal-only; LLM/embed adımlarını atla)
db-agent show demo/DemoDB/dbo/SP_TEKLIF_SURELERI --sql  # + summary/category
db-agent deps demo/DemoDB/dbo/SP_TEKLIF_SURELERI       # calls/reads/writes
db-agent deps demo/DemoDB/dbo/TEKLIF --in              # bu tabloyu kim yazıyor/okuyor
db-agent table demo/DemoDB/dbo/TEKLIF                  # kolon/PK/FK/check + okuyan/yazan
db-agent catalog --server demo --database DemoDB       # kod+veri taksonomisi + kategori sayıları
db-agent status                                        # katalog + embeddings + son run'lar
```

> Konfigürasyon: `config/servers.yaml` (sunucu/DB/exclusion) + `.env` (şifreler). Örnekler:
> `config/servers.example.yaml`, `.env.example`. `data/` lokal disk store (gitignore).

## Testler

```bash
pytest            # birim + sync pipeline entegrasyonu (Docker gerektirmez)
```

`tests/test_sync_pipeline.py` tüm M1+M2 orkestrasyonunu (gerçek parser + disk store + in-memory
kaynak/repo) Docker olmadan doğrular. Postgres'e özgü SQL (recursive CTE, migration) ve gerçek
MSSQL çekimi docker-compose ile end-to-end doğrulanır.
