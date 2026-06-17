# 02 — Bağlantı ve Keşif

## Amaç

Sisteme **sadece bir sunucu bağlantısı** verildiğinde, içindeki database'leri kendi bulması; veya belirli DB'ler verildiğinde sadece onları kapsaması. Çok-sunuculu / çok-DB'li. Mevcut GitHub repo'sundan bağımsız — git yok, doğrudan canlı keşif.

> **Genişletilebilirlik:** Tüm MSSQL'e özel mantık (`sys.sql_modules`, `sys.databases` vb.) `infrastructure/source/mssql`'de, `SourceDbPort` (application) arkasındadır. İleride başka bir kaynak (PostgreSQL/Oracle) eklemek = aynı port'u implemente eden yeni bir adapter; çekirdek (parse/index/agent) değişmez. Şu an kapsam yalnızca MSSQL.

## Bağlantı yöntemi

- **Sürücü:** `pyodbc` + ODBC Driver 18 for SQL Server. (Alternatif: `pymssql` — kurulumu kolay ama ODBC kadar özellikli değil; `pyodbc` öneri.)
- **Kimlik doğrulama:** **SQL authentication** (kullanıcı/şifre). `auth` alanı config'te ileride genişletilebilir biçimde durur ama şimdilik tek değer: `sql`. (Windows/Kerberos ve Azure AD bilinçli olarak kapsam dışı — sadeleştirme.)
- **Erişim:** Salt-okunur servis hesabı (yetki script'i aşağıda). Sistem hiçbir DML/DDL çalıştırmaz.
- **Prod koruması — `ApplicationIntent=ReadOnly`:** Sunucu bir Availability Group listener'ı ise bağlantı okunabilir **secondary**'e yönlenir; birincil yük almaz. AG yoksa etkisizdir, doğrudan birincile bağlanır. Katalog sorguları zaten hafiftir.
- **Kimlik etiketi (audit):** Connection string'e `APP=db-agent-catalog`. DBA `sys.dm_exec_sessions`'da aracın oturumlarını görüp izleyebilir. Maliyeti sıfır.
- **Bağlantı havuzu:** Sunucu başına küçük bir pool; keşif/extraction sıralı, ağ dostu.
- **Dayanıklılık:** Geçici hata/timeout'ta retry + exponential backoff. Sunucu erişilemezse o sunucu **degraded** işaretlenir, son iyi snapshot korunur, run diğer sunucularla devam eder (bkz. "Dayanıklılık ve degraded").

## Config modeli — `config/servers.yaml` + `.env`

Karar: **YAML config + .env secrets.** Sunucu/DB listesi ve kapsam YAML'da; şifreler `.env`/ortam değişkeninde. Böylece config git'e girebilir, secret girmez.

```yaml
# config/servers.yaml
defaults:
  driver: "ODBC Driver 18 for SQL Server"
  auth: "sql"                          # şimdilik tek desteklenen değer
  encrypt: true
  trust_server_certificate: true       # kurum içi sertifika senaryosu
  application_intent: "ReadOnly"        # AG varsa secondary'e yönlen
  app_name: "db-agent-catalog"          # audit etiketi
  schedule: "0 3 * * *"                # her gece 03:00 (cron ifadesi)
  embedding_model: "bge-m3"            # global varsayılan (bkz. 07)
  resilience:
    max_retries: 3
    backoff_seconds: 5
  # yeni DB belirirse: önce keşfet, indekslemeyi onaya bağla
  new_database_policy: "discover_then_approve"   # | auto | explicit_only

servers:
  - id: "prod-sql-01"
    host: "10.0.0.21,1433"
    username_env: "PROD01_USER"        # .env'deki değişken adı
    password_env: "PROD01_PASS"
    # discovery: hangi DB'ler?
    databases: "auto"                  # auto = sunucudaki tüm user DB'leri keşfet
    exclude_databases: ["tempdb", "model", "msdb", "master"]
    approved_databases: ["UretimDB"]   # auto+approve modunda indekslenmesi onaylılar
    include_schemas: "all"             # ya da ["dbo", "sales"]
    object_types: ["procedure", "view", "function", "trigger", "table"]
    # isim filtreleri (sistem nesneleri zaten is_ms_shipped ile elenir)
    exclude_object_patterns: ["tmp_*", "bkp_*", "*_old"]
    include_object_patterns: "all"

  - id: "kasko-sql"
    host: "10.0.0.34"
    username_env: "KASKO_USER"
    password_env: "KASKO_PASS"
    databases: ["KaskoDB", "TeklifDB"] # sadece bu DB'ler (explicit → onay gerekmez)
    schedule: "0 */6 * * *"            # sunucuya özel override

# Dışlama (exclusion) — anlamsal/kritik gizlilik (14). Çok-seviye + glob + tip.
# Dışlanan: çekilmez, indekslenmez, aranmaz, cevapta GEÇMEZ, varlığı ifşa edilmez.
# Geri alınabilir: buradan çıkarınca bir sonraki sync'te yeniden dahil edilir.
exclusions:
  - server: "kasko-sql"                # zorunlu
    database: "KaskoDB"                # opsiyonel (yoksa tüm DB'ler)
    schema: "dbo"                      # opsiyonel
    types: ["table", "procedure"]      # opsiyonel (yoksa tüm tipler)
    names: ["MAAS_BORDRO", "dbo.TCKIMLIK_LOG"]   # tam ad
    patterns: ["*_SECRET", "TCKIMLIK_*", "*_PII"] # glob
    reason: "PII / kritik gizlilik"    # audit için
```

```dotenv
# .env  (git'e GİRMEZ — .gitignore'da)
PROD01_USER=svc_catalog_ro
PROD01_PASS=********
KASKO_USER=svc_catalog_ro
KASKO_PASS=********
```

### Neden bu model?
- **Alternatif A — tek connection string env:** Çok-sunucu için ölçeklenmez, kapsam/schedule taşıyamaz.
- **Alternatif B — şifreli secrets store (SQLCipher/age):** Daha güvenli ama anahtar yönetimi ekler; v2'de opsiyon olarak eklenebilir.
- **Alternatif C — OS keyring/Vault:** Kurumsal; lokal-basitlik hedefinden uzak.
- **Seçim:** YAML+.env — standart, şeffaf, çok-sunucuyu temiz taşır. (Şifreli store sonradan eklenebilir; arayüz aynı kalır.)

## En-az-yetki (servis hesabı grant script'i)

DBA'e verilecek somut script. Sistem fazla yetki istemez; salt-okunur katalog erişimi yeter:

```sql
-- Sunucu seviyesi (login)
CREATE LOGIN svc_catalog_ro WITH PASSWORD = '***';

-- Keşfedilecek her DB'de:
CREATE USER svc_catalog_ro FOR LOGIN svc_catalog_ro;
GRANT VIEW DEFINITION TO svc_catalog_ro;        -- tanımlar + dm_sql_referenced_entities
GRANT VIEW DATABASE STATE TO svc_catalog_ro;    -- satır sayısı / istatistik DMV'leri (05)
-- CONNECT zaten user ile gelir; veri SELECT yetkisi VERİLMEZ (profilleme kapalı, 05)
```
> Not: `VIEW SERVER STATE` yalnızca AG/replica durumunu okumak istenirse opsiyoneldir. Veri tablolarına `SELECT` **bilinçli olarak yok** — sistem canlı veri okumaz.

## Keşif (discovery) akışı

1. **Sunucuya bağlan** (master üzerinden; `ApplicationIntent=ReadOnly`).
2. **Database listesi:**
   ```sql
   SELECT name, database_id, create_date
   FROM sys.databases
   WHERE database_id > 4              -- sistem DB'lerini atla
     AND state = 0                    -- sadece ONLINE
     AND HAS_DBACCESS(name) = 1;      -- erişebildiklerimiz
   ```
   `databases: "auto"` ise hepsi (exclude çıkarılır); liste verildiyse sadece onlar.
3. **Yeni DB onay kapısı:** `auto` modda, daha önce görülmemiş bir DB **keşfedilir ama otomatik indekslenmez**. `approved_databases`'te yoksa `pending` olarak işaretlenir, run raporunda "onay bekliyor" listelenir. Onaylanınca (config'e eklenince) ilk tam indeksleme yapılır. (Sürpriz crawl + istemsiz cloud maruziyeti önlenir.)
4. **Her DB için nesne envanteri (sistem nesneleri elenir):**
   ```sql
   SELECT s.name AS schema_name, o.name AS object_name, o.type_desc,
          o.object_id, o.modify_date
   FROM sys.objects o
   JOIN sys.schemas s ON s.schema_id = o.schema_id
   WHERE o.type IN ('P','V','FN','IF','TF','TR')   -- SP, View, Func, Trigger
     AND o.is_ms_shipped = 0;                       -- Microsoft kurulu nesneleri ele
   -- ayrıca config'teki include/exclude isim desenleri uygulanır
   ```
   Tablolar için `sys.tables` + şema (bkz. `05`).
5. **Synonym keşfi + çözümleme:** `sys.synonyms` okunur; her synonym `base_object_name` (3-parçalı ad) ile gerçek hedefe çözülür. Arama/bağımlılıkta hem synonym adı hem hedef bilinir ("X aslında DB_B.dbo.Y'ye gidiyor").
6. **Cross-DB bağımlılıklar:** `sys.sql_expression_dependencies`'te `referenced_database_name` dolu olan kenarlar cross-DB olarak işaretlenir; hedef başka bir keşfedilmiş+onaylı DB ise graph'ta gerçek düğüme bağlanır (bkz. `04`). Hedef DB **kapsam dışıysa kenar düşürülür** (external düğüm yaratılmaz — `04` kararı).
7. **UDT / kullanıcı-tanımlı tipler:** `sys.types (is_user_defined=1)` ve table-valued parametre tipleri (`sys.table_types`) envantere alınır; parametre/şema çözümlemesinde kullanılır.
8. **Dışlama (exclusion) filtresi:** `exclusions` ile eşleşen nesneler/tablolar envanterden **tamamen çıkarılır** — tanımı bile çekilmez (extraction'a hiç girmez). Daha önce indekslenmiş bir nesne yeni dışlamaya uydu ise **purge** edilir (disk + Postgres + kategori). Manifest'te dışlanan sayısı raporlanır (ad ifşa edilmeden). Detay: `14`.
9. **Envanteri manifest'e yaz:** `data/<server>/<db>/_manifest.json` (disk = otorite, `01`). Bir sonraki çalışmada değişim tespitinin temeli.

## Keşif çıktısı — `_manifest.json` (örnek)

```json
{
  "server": "kasko-sql",
  "database": "KaskoDB",
  "discovered_at": "2026-06-17T03:00:11Z",
  "status": "active",
  "object_count": {"procedure": 1840, "view": 220, "function": 95, "trigger": 40,
                   "table": 510, "synonym": 60, "user_type": 18},
  "objects": [
    {"schema": "dbo", "name": "SP_TEKLIF_SURELERI", "type": "procedure",
     "object_id": 1234567, "modify_date": "2026-05-02T09:15:00Z", "hash": null}
  ],
  "synonyms": [
    {"schema": "dbo", "name": "TEKLIF_V", "base": "ArsivDB.dbo.TEKLIF", "cross_db": true}
  ]
}
```
`hash` ilk çekimde doldurulur; sonraki çalışmalarda `modify_date` + `hash` ile değişen tespiti yapılır (bkz. `03`).

Sunucu seviyesinde ayrıca `data/<server>/_server.json`: keşfedilen DB'ler, `pending` (onay bekleyen) DB'ler ve `degraded` durumu burada tutulur.

## Dayanıklılık ve degraded durumu

- **Retry/backoff:** geçici hata/timeout `max_retries` kez, üstel backoff ile tekrarlanır.
- **Degraded sunucu:** Tüm retry'lar tükenirse sunucu `degraded` işaretlenir; **son iyi snapshot** (disk store + indeks) olduğu gibi kalır, run **patlamaz**, diğer sunucularla devam eder. Bir sonraki schedule'da yeniden denenir.
- **Kısmi DB hatası:** Bir DB erişilemezse sadece o DB atlanır, aynı sunucudaki diğer DB'ler işlenir.
- **Backpressure:** Kaynağa karşı eşzamanlılık sınırlı (prod'u yormamak — `01` değişmezi); farklı sunucular paralel, aynı sunucu içi seri.
- Tüm bu durumlar run-store'a (`runs`) ve log'a yazılır (`01` platform katmanı).

## Güvenlik notları

- Kaynak DB kullanıcısı **read-only** servis hesabı; veri tablolarına `SELECT` yetkisi yok (yukarıdaki grant script).
- `.env` ve `config/secrets.*` `.gitignore`'da.
- Bağlantı log'larında şifre maskelenir; `app_name` ile oturumlar DBA tarafından izlenebilir.
- `encrypt: true` + sertifika doğrulama kurum politikasına göre ayarlanır.
- `new_database_policy: discover_then_approve` → yeni DB istemeden indekslenip (cloud açıksa) dışarı gönderilmez.

## Açık sorular (sonra netleşecek)

- Bir sunucuda yüzlerce DB varsa keşif paralelliği / rate-limit sınırı ne olmalı? (varsayılan: sunucu-içi seri, sunucular-arası paralel)
- `degraded` bir sunucu kaç başarısız denemeden sonra bildirim/alarm üretmeli? (`11` webhook)
