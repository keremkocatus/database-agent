# 01 — Mimari

## Bakış açısı

Sistem iki ana zaman ekseninde çalışır:

- **Indexing-time (offline / schedule'lı):** Keşif → Extraction → Parsing → Enrichment → Embedding → Index. Yavaş çalışabilir, LLM'i bolca kullanır. Çıktısı: lokal store + PostgreSQL indeksi.
- **Query-time (online / kullanıcı sorduğunda):** Retrieve → Rerank → Agent (ReAct) → Cevap. Hızlı olmalı; LLM'i ölçülü kullanır.

İki eksen **aynı veri katmanını** paylaşır (lokal store + Postgres). Aralarındaki tek köprü budur — gevşek bağlı (loosely coupled).

## Bileşenler

| Bileşen | Rol | Zaman |
|---|---|---|
| **Connector** | MSSQL bağlantısı, sunucu/DB keşfi | indexing |
| **Extractor** | Nesne tanımları + tablo şeması çekme | indexing |
| **Store** | Lokal dosya deposu (tanımlar + metadata) | her ikisi |
| **Parser** | sqlglot ile yapısal metadata + bağımlılık | indexing |
| **Enricher** | LLM ile özet, kategori, tablo açıklaması | indexing |
| **Categorizer / Folderer** | Hibrit klasörleme + klasör metadata | indexing |
| **Embedder** | BGE-M3 ile object card + chunk vektörleri | indexing |
| **Index (Postgres)** | pgvector (dense+sparse) + pg_trgm + metadata tabloları | her ikisi |
| **Retriever** | Hybrid arama + RRF + reranker | query |
| **LLM Provider Layer** | Provider-agnostik LLM/embedding erişimi | her ikisi |
| **Agent** | ReAct loop, araçları çağırır | query |
| **Scheduler** | APScheduler + CLI, sync **job'u kuyruğa atar** | indexing (API proc.) |
| **Job Queue** | Postgres-tabanlı iş kuyruğu (`FOR UPDATE SKIP LOCKED`) | köprü |
| **Worker** | Kuyruktan job alır, indexing pipeline'ını çalıştırır | indexing (ayrı proc.) |
| **Reconciler** | Disk ↔ Postgres bütünlük/drift kontrolü ve hizalama | indexing |
| **Platform** | Config/secrets yükleme, structured log, trace, run-store, DB migration | çapraz-kesen |
| **API** | FastAPI REST + CLI; sorgu + yönetim | query (API proc.) |

## Veri akışı (indexing-time)

```
                        ┌─────────────────────────────────────────────┐
                        │  config/servers.yaml  +  .env (secrets)      │
                        └───────────────┬─────────────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (1) DISCOVERY  │ Connector: sunucuya bağlan → DB'leri,      │
                  │ şemaları, nesneleri listele                │
                  └─────────────────────┬─────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (2) EXTRACT    │ Extractor: sys.sql_modules → tanımlar      │
                  │ INFORMATION_SCHEMA/sys → tablo şeması      │
                  │ modify_date + SHA256 ile değişeni seç      │
                  └─────────────────────┬─────────────────────┘
                                        │  (sadece değişenler)
                  ┌─────────────────────▼─────────────────────┐
   (3) STORE      │ Lokal store: data/<server>/<db>/<schema>/  │
                  │   procedures|views|functions|triggers/*.sql │
                  │   tables/*.json                            │
                  └─────────────────────┬─────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (4) PARSE      │ Parser (sqlglot): parametreler, dönen tip, │
                  │ kullanılan tablolar, çağrılan nesneler →   │
                  │ bağımlılık grafiği                         │
                  └─────────────────────┬─────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (5) ENRICH     │ Enricher (LLM): nesne özeti + kategori,    │
                  │ tablo/kolon açıklaması                     │
                  └─────────────────────┬─────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (6) FOLDER     │ Categorizer: hibrit klasörleme +          │
                  │ klasör README.md + catalog.json           │
                  └─────────────────────┬─────────────────────┘
                                        │
                  ┌─────────────────────▼─────────────────────┐
   (7) EMBED+INDEX│ Embedder (BGE-M3): object card + chunk →  │
                  │ Postgres: pgvector(dense+sparse)+trgm+meta │
                  └────────────────────────────────────────────┘
```

## Veri akışı (query-time)

```
  kullanıcı sorusu
        │
        ▼
  ┌───────────────┐   ┌──────────────────────────────────────────┐
  │ API / CLI     │──▶│ Agent (ReAct loop)                       │
  └───────────────┘   │                                          │
                      │  think → tool seç → gözlemle → tekrar     │
                      │   tools:                                 │
                      │   • search_objects (hybrid+rerank)       │
                      │   • read_object / read_summary           │
                      │   • get_dependencies                     │
                      │   • search_tables / describe_table       │
                      │   • add_to_shortlist / finish            │
                      └───────────────┬──────────────────────────┘
                                      │
                      ┌───────────────▼──────────────┐
                      │ Retriever                     │
                      │  vektör (pgvector) ─┐         │
                      │  sparse + trigram ───┼─ RRF ─▶ cross-encoder rerank
                      │                                │
                      └───────────────┬──────────────┘
                                      │
                            LLM Provider Layer (lokal/cloud)
                                      │
                                 final cevap + kaynak nesneler
```

## Katman bağımsızlığı

- **Extraction/Parsing/Store** LLM'siz çalışır → kaynak DB değişmese bile bu katman tek başına test edilebilir.
- **LLM Provider Layer** tek geçit → modeli/provider'ı değiştirmek tek config değişikliği.
- **Retriever** agent'tan bağımsız → CLI'dan "ham arama" da yapılabilir (agent olmadan).
- **Scheduler** sadece indexing pipeline'ını tetikler → query tarafı her zaman ayakta kalır.

## Süreç topolojisi (API + Worker, baştan ayrık)

İki ayrı process, ortak Postgres üzerinden konuşur. Indexing'in ağır yükü (embedding, LLM) API'nin sorgu gecikmesini **hiç** etkilemez.

```
┌──────────────────┐        Postgres (ortak)        ┌──────────────────────┐
│  API process     │   ┌────────────────────────┐   │  Worker process      │
│  • FastAPI/REST  │   │ jobs  (kuyruk)         │   │  • job poller        │
│  • CLI sorgu     │──▶│ objects/embeddings/... │◀──│  • indexing pipeline │
│  • Agent (ReAct) │   │ runs  (run-store)      │   │  • reconciler        │
│  • Scheduler ────┼──▶│ (job enqueue)          │   │  • embedder/LLM/GPU  │
└──────────────────┘   └────────────────────────┘   └──────────────────────┘
```

- **İş kuyruğu = Postgres tablosu.** `jobs(id, server, type, state, payload, locked_by, locked_at)`; worker `SELECT … FOR UPDATE SKIP LOCKED` ile job çeker. Redis/Celery gibi ekstra servis yok → "tek altyapı" korunur.
- **Scheduler API process'inde** yaşar ama işi kendi yapmaz; sadece zamanı gelince kuyruğa job atar. Worker(lar) işi yürütür. Böylece worker'ı yatay ölçeklemek = ikinci bir worker başlatmak.
- **Aynı binary, iki rol:** `db-agent serve` (API) ve `db-agent worker` (worker) aynı `core` kütüphanesini kullanır; sadece giriş noktası farklı. İstenirse tek makinede yan yana, istenirse ayrı makinelerde.

## Otorite (source of truth) ve reconciliation

Otorite **alana göre bölünür**, böylece "ikisi de otorite" çakışmaya değil net sahipliğe dönüşür:

| Veri | Otorite | Yeniden üretilebilir? |
|---|---|---|
| Ham SQL tanımları, `meta.json`, tablo JSON, katalog README/JSON, changelog | **Disk store** | kaynaktan re-sync |
| Postgres **türetilmiş indeks**: embedding (dense+sparse), arama, graph kenarları | **Postgres (atılabilir)** | **Evet** — diskten `reindex` |
| Postgres **otoriter durum**: `search_feedback` (18), `chat_*` (17), `runs`/trace (16) | **Postgres (otoriter)** | **HAYIR** — yalnızca burada |
| Çakışan alanlar (özet, kategori, hash, parametreler) | Disk yazar → Postgres'e **upsert** | türetilmiş |

> **Önemli:** Postgres tek-tip değil. İndeks kısmı atılabilir (diskten kurulur); ama feedback/chat/runs **yalnızca** Postgres'te otoriterdir → yedeklemeye dahil edilmeli (DR: `19`). "Postgres tamamen disposable" sadece indeks için doğrudur.

- **Reconciler** periyodik (ve sync sonunda) çalışır: her iki taraftaki `id + hash` kümesini karşılaştırır.
  - Diskte olup Postgres'te olmayan / hash'i farklı → Postgres'e yeniden indeksle.
  - Postgres'te olup diskte olmayan (kaynakta silinmiş) → Postgres'ten düşür.
  - Tutarsızlık sayısı bir **drift raporu** olarak run-store'a yazılır.
- **Felaket kurtarma:** Postgres **indeksi** kaybolsa diskten `reindex` ile kurulur. Ama **otoriter durum** (feedback/chat/runs) diskte yok → düzenli `pg_dump` ile yedeklenir. Tam DR = disk store + config + Postgres-otoriter dump (`19`).

## Tutarlılık: transactional per-object upsert

- Her nesne **kendi transaction'ında** upsert edilir (`objects` + `embeddings` + `edges` birlikte commit). Sorgu hiçbir zaman yarı-yazılmış nesne görmez.
- Model **eventually consistent:** yeni/değişen nesne sync ilerledikçe görünür hale gelir; sorgu o an indekste ne varsa onu döndürür, asla bloke olmaz ("her zaman cevap ver").
- Silme de aynı transaction modelinde (`ON DELETE CASCADE`).

## Platform katmanı (çapraz-kesen)

Tüm bileşenlerin altında ince bir platform katmanı:
- **Config + secrets:** YAML + `.env` yükleme, doğrulama (pydantic), `allow_cloud` gibi guard'lar (`02`,`09`).
- **Structured logging:** JSON log; her kayıtta `server/db/object_id/run_id` korelasyon alanları.
- **Run-store + trace:** `runs` tablosu (sync özeti, değişim sayıları, hatalar) + agent sorgu trace'i (kullanılan araçlar, iterasyon, latency).
- **DB migration:** Numaralı **SQL-dosya migration + runner** (ORM yok; `13`); `meta.json` içinde `schema_version`. Şema değişikliği full-rebuild gerektirmeden uygulanır.
- **Retention:** `runs`/trace ve `_changelog` sınırsız büyümesin diye yapılandırılabilir saklama penceresi (ör. run/trace 90 gün, sonra özetlenip arşiv/silme); periyodik temizlik job'u (`11`).
- **DB erişimi:** SQLAlchemy **async engine** + ham `text()` SQL (Outfit `DatabaseClient` deseni); repository'ler `application/ports`'u implemente eder; **DB-tarafı RPC/function yok**.

## Mimari değişmezler (invariants)

1. **Query-time asla kaynak MSSQL'e dokunmaz.** Sorgu anında yalnızca Postgres + lokal store okunur. Canlı DB'ye bağlantı sadece indexing-time'da (worker) kurulur. → prod DB korunur, sorgu hızlı.
2. **Kaynak DB salt-okunur.** Hiçbir bileşen kaynağa yazmaz.
3. **Backpressure / rate-limit:** Worker kaynak MSSQL'e karşı eşzamanlılığı sınırlar (prod'u yormaz); cloud LLM/embedding çağrılarında kota/maliyet için throttle + retry/backoff (`09`).
4. **Disk = içerik otoritesi; Postgres indeksi yeniden kurulabilir, ama Postgres otoriter durumu (feedback/chat/runs) yedeklenmeli** (yukarıdaki tablo + `19` DR).

## Önemli mimari kararların gerekçesi

- **Neden iki ayrık eksen?** Indexing ağır ve LLM-yoğun; query hızlı olmalı. Ayırmak, indexing çökse bile aramanın çalışmaya devam etmesini sağlar.
- **Neden tek veri katmanı (Postgres)?** Vektör + keyword + metadata tek yerde; "dengeli/basit" önceliğine uygun, ayrı servis bakımı yok. (Bkz. `07`.)
- **Neden framework yok?** ReAct döngüsü ve incremental sync, custom kodla tam kontrol + sıfır sürpriz; provider-agnostik hedefiyle de çelişmez. (Bkz. `09`, `10`.)
- **Neden API + Worker baştan ayrık?** Indexing GPU/CPU-yoğun; aynı process'te koşsa büyük reindex sorgu latency'sini fırlatır. Ayrı worker, "güncellenirken bile cevap ver" güvencesini verir. Maliyeti düşük tuttuk: kuyruk ekstra servis değil, Postgres tablosu.
- **Neden alana-bölünmüş otorite + reconciliation?** Disk içerik için, Postgres türetilmiş indeks için doğal otorite. Bölünce drift yönetimi tek yönlü ve sade kalır; Postgres her an diskten yeniden kurulabilir (atılabilir indeks).
- **Neden eventually-consistent upsert?** Anlık-tutarlı versiyon-swap incremental'da aşırı; nesne-başı transaction hem yarı-yazılmış hali engeller hem sorguyu asla bloke etmez.
