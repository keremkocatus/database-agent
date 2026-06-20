# CLAUDE.md — db-agent çalışma kılavuzu

Bu dosya, bu repoda çalışırken uyman gereken proje standartlarını ve `design/` dokümanlarını
nasıl kullanacağını tanımlar. Genel davranış kuralları için kullanıcının global ayarları geçerli;
buradakiler **bu projeye özgü** kararlardır.

## Tek cümlede sistem
Doğrudan bağlantı verilen MSSQL sunucularını keşfeden; SP/View/Function/Trigger + tablo şemasını
lokale indiren; yapısal metadata + bağımlılık grafiği üreten; (ileride) agentic biçimde
klasörleyip arayan, kendini güncelleyen, lokal, açık kaynak bir katalog sistemi.

## Şu anki durum
**M0–M2 (LLM'siz deterministik çekirdek) tamamlandı.** M3+ (provider/embedding/retrieval/agent/
scheduling/serving/sertleştirme) henüz yok ama port arayüzleri hazır. Roadmap: `design/13`.

---

## 1) `design/` dokümanları = otorite
- `design/00`–`design/20` sistemin **karar kaydıdır**; kod bu kararlara dayanır. README: `design/README.md`.
- **Bir katmana/özelliğe dokunmadan önce ilgili tasarım dokümanını oku.** Kod yorumları zaten ilgili
  dosyayı işaret eder (ör. `# design/03`, `(design/04)`). Eşleme:

  | Konu | Doküman |
  |---|---|
  | Mimari, veri akışı, otorite/invariant'lar | `01` |
  | Bağlantı + keşif (config, exclusion, discovery sorguları) | `02` |
  | Extraction + disk store + değişim tespiti (modify_date+hash) | `03` |
  | Parsing + bağımlılık grafiği (sqlglot + server-side) | `04` |
  | Tablo sözlüğü (kolon/PK/FK/check, LLM açıklama) | `05` |
  | Kategorizasyon/foldering (M4) · embedding (M4) · retrieval (M5) | `06`/`07`/`08` |
  | LLM provider · agent runtime · scheduling · serving | `09`/`10`/`11`/`12` |
  | Tech stack + repo iskeleti + **roadmap M0–M8** | `13` |
  | Güvenlik (exclusion, per-user, injection, redaction-yok) | `14` |
  | Test/eval · observability · chat · etkileşim · deployment · kapasite | `15`–`20` |

- **Tasarımdan sapma gerekiyorsa:** önce bunu söyle, gerekçeyi `design/` ile çeliştir. Karar
  değişiyorsa ilgili tasarım dokümanını da güncelle (kod ile doküman tutarlı kalsın). Tasarımı
  sessizce ihlal eden kod yazma.
- Kapsam genişletmeden önce roadmap'e bak: bir iş M3+ ise **şimdi yapma**, port'un arkasında stub bırak.

## 2) Mimari standartları (Clean Architecture — Outfit deseni)
Bağımlılık yönü **tek yönlü**:
```
cli / api / worker  →  application  →  domain
                          ▲
                    infrastructure  (yalnızca application/ports'u implemente eder)
```
- **`domain/`** — saf iş kuralları (framework bağımsız dataclass + servis). Hiçbir şey import etmez
  (ne pydantic, ne sqlalchemy, ne pyodbc).
- **`application/`** — use-case'ler + `ports/` (Protocol arayüzleri) + dtos. Use-case'ler **yalnızca
  port'lara** bağımlıdır; infrastructure'ı **asla** doğrudan import etmez.
- **`infrastructure/`** — port implementasyonları (MSSQL adapter, Postgres repo'ları, disk store,
  sqlglot parser, settings) + `container.py` (Composition Root: tüm wiring burada).
- **`cli/`,`api/`,`worker/`** — ince delivery kabuğu; yalnızca `Container`'dan use-case alır, çağırır.
  İş mantığı koymaz.

**Yeni bir şey eklemek = yeni adapter veya yeni use-case (port arkası).** Katman atlamayan, port
arkasına yazılan kod ekle. Yeni dış bağımlılık (DB/servis/model) → yeni port + adapter.

## 3) Sabit kararlar (değiştirme — `design/01`/`13`/`14`)
- **Salt-okunur kaynak.** Kaynak MSSQL'e asla DML/DDL yazma. Query-time kaynağa **hiç** dokunmaz
  (yalnızca Postgres + disk okunur).
- **DB erişimi:** SQLAlchemy **async engine + ham `text()` SQL**. ORM modeli **yok**, DB-tarafı
  stored procedure/RPC/function **yok**. Migration = numaralı **SQL-dosya** + runner (`migrations/`).
- **Tek veri katmanı:** PostgreSQL + pgvector + pg_trgm. Ayrı vektör/graph DB **yok** (graph =
  `edges` tablosu + recursive CTE).
- **Otorite ayrımı:** disk = içerik otoritesi (ham SQL, meta, manifest, changelog); Postgres indeksi
  (objects/embeddings/edges) **atılabilir** (diskten reindex); ama feedback/chat/runs **otoriter** →
  yedeklenir. Ham SQL gövdesi **diskte**, Postgres'te değil (`design/19` risk notu).
- **Hibrit kimlik:** `uid = server/database/object_id` (kalıcı, rename'de sabit) + `alias =
  server/database/schema/name` (okunur). Eşleştirme **object_id** üzerinden → rename = taşıma,
  delete+add değil (`design/03`). Helper: `src/domain/value_objects/identity.py`.
- **Değişim tespiti:** modify_date (ucuz aday) → `SHA256(normalize(sql))` (kesin). Soft-delete
  güvenliği: keşif tam başarılı değilse eksik nesne **silinmez** (`design/03`).
- **Exclusion (`design/14`):** eşleşen nesne çekilmez/indekslenmez/aranmaz, varlığı ifşa edilmez.
  `ServersConfig.is_excluded(...)` keşif sırasında uygulanır. **Redaction yok** — hassas içerik
  sorumluluğu `allow_cloud`/provider seçiminde.
- **Sistem kilitlenmez:** paylaşılan kaynak = havuz + timeout + kuyruk (`design/20`, M7+).
- **Framework yok:** LangChain/LlamaIndex/LiteLLM/CrewAI/ORM **eklenmez** (`design/13`). İnce custom
  adapter + custom ReAct (M6).

## 4) Kod konvansiyonları
- **Dil:** kod İngilizce isimler; **yorumlar Türkçe** ve ilgili tasarım dokümanını parantezle işaret
  eder (ör. `(design/04)`). Mevcut dosyaların yorum yoğunluğunu ve üslubunu taklit et.
- **Async:** Postgres repo'ları + use-case'ler `async`. Kaynak DB adapter'ı (pyodbc) **senkron** —
  inline pipeline'da doğrudan çağrılır. `pyodbc` **lazy import** (ODBC driver olmadan testler çalışsın).
- **DTO/entity:** saf `@dataclass`. Disk JSON şekli entity'nin `*_dict()` metodundan üretilir.
- **Yeni Postgres tablosu/kolonu:** yeni numaralı `migrations/NNNN_*.sql` (idempotent: `IF NOT EXISTS`).
  Mevcut migration dosyasını **değiştirme** (uygulananlar `schema_migrations`'ta izlenir); yeni dosya ekle.
- **Config:** YAML (`config/servers.yaml`) yapısal kapsam; `.env` secret. Pydantic Settings
  (`infrastructure/settings/config.py`). Secret'i koda/YAML'a yazma.

## 5) Test standartları
- Testler `tests/unit/` altında, `pytest` (asyncio_mode=auto). Dış servis (Postgres/MSSQL) **gerektirmez**.
- Port'lar için **in-memory fake** kullan (örnek: `tests/unit/test_sync_pipeline.py` — gerçek parser +
  gerçek disk store + sahte kaynak/repo ile tüm sync pipeline'ını doğrular).
- Postgres'e özgü SQL (recursive CTE, migration) ve gerçek MSSQL çekimi **docker-compose** ile
  end-to-end doğrulanır (birim testin kapsamı dışı).
- Değişiklikten sonra: `pytest` yeşil + `python -m compileall src` temiz olmalı.

## 6) Sık komutlar
```bash
pip install -e ".[dev]"                 # kurulum (venv: .venv)
pytest                                  # birim + pipeline testleri
docker compose up -d                    # postgres(pgvector) + seed mssql (DemoDB)
db-agent init | doctor                  # kurulum + ön-uçuş kontrolü
db-agent sync --server demo --inline    # discover→extract→parse→tablo sözlüğü→Postgres
db-agent show|deps|table|status ...     # yapısal metadata + graph sorgu
```

## 7) Ortam notları
- Windows + PowerShell. CLI çıktısı Windows konsol cp1252'de patlamasın diye `cli/main.py` stdout'u
  UTF-8'e sabitler — yeni delivery girişlerinde bunu koru.
- Docker bu makinede **kurulu olmayabilir**; yoksa Postgres/MSSQL gerektiren adımları çalıştıramazsın,
  bunu açıkça belirt ve birim/fake testlerle doğrula.
- `data/`, `.env`, `config/servers.yaml`, `.venv` git'e girmez (gitignore).
