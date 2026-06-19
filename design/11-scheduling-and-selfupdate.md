# 11 — Scheduling ve Self-Update

## Amaç

Sistem kendini belirli aralıklarla güncellesin: kaynak MSSQL'i tarayıp değişenleri lokale çeksin, yeniden işleyip indeksi tazelesin — minimum müdahaleyle. Karar: **APScheduler (API process'inde) kuyruğa job atar; Worker(lar) çalıştırır** (`01` topoloji). CLI de aynı kuyruğu besler.

## Scheduler → kuyruk → worker (01 ile uyumlu)

- **Scheduler (API process):** Cron zamanı gelince işi **kendi yapmaz**, Postgres `jobs` tablosuna job ekler. Ekstra servis yok.
- **Worker (ayrı process):** `SELECT … FOR UPDATE SKIP LOCKED` ile job çeker, pipeline'ı yürütür. Yatay ölçek = ikinci worker.
- **CLI:** `db-agent sync` aynı kuyruğa job atar (debug için `--inline` ile o anda çalıştırır — karar).
- **Coalescing:** Aynı kapsam için bekleyen/çalışan job varsa ikinci kez eklenmez.
- **Öncelik:** Manuel `sync --now` scheduled job'ları geçer (priority alanı).

> GitHub Actions elendi: runner'ın kurum-içi MSSQL'e/GPU'ya erişimi yok. Sistem lokal çalışır.

## Job tipleri ve granenlik (per-object)

Karar: **iş birimi = nesne (per-object).** En iyi resume + paralellik. Kuyruk tipleri:
- `discover(server[,db])` — keşif + diff (02/03); çıktısı değişen nesneler → **fan-out** per-object job.
- `object(uid)` — tek nesnenin extract→parse→enrich→categorize→embed→index zinciri.
- `table(uid)` — tablo/view şema + sözlük + embed (05).
- `reconcile(server,db)` — disk↔Postgres drift (01).
- `taxonomy(server,db)` — embedding-kümeleme + etiketleme (06).

**Per-object overhead'i azaltma (önemli):** İlk backfill'de 2000+ `object` job oluşur. Worker bunları **batch dequeue** eder (N nesneyi birden çeker) → BGE-M3 embedding ve LLM çağrıları **batch**'lenir; kuyruk volümü yönetilebilir kalır. `discover` ucuz ve tekildir; ağır iş per-object job'larda paralelleşir.

## Zamanlama config

`config/servers.yaml`'da global + sunucu-bazlı cron (bkz. `02`):
```yaml
defaults:
  schedule: "0 3 * * *"        # her gece 03:00
servers:
  - id: "kasko-sql"
    schedule: "0 */6 * * *"    # 6 saatte bir (override)
```
APScheduler bu cron ifadelerini sunucu başına job olarak kurar.

## Incremental sync akışı (her tetiklemede)

```
A. discover job:
   1. DISCOVERY  → sunucu/DB envanteri (02), _manifest ile karşılaştır
   2. DIFF       → eklenen / silinen / (modify_date adayı → hash doğrulama) (03)
   3. FAN-OUT    → değişen her nesne için bir object/table job kuyruğa
B. object(uid) job (batch dequeue, paralel worker):
   4. EXTRACT    → tanım + (tablo ise) şema
   5. PARSE      → yeniden parse, bağımlılık kenarları (04)
   6. ENRICH     → özet/açıklama (human_description öncelikli, 03/05)
   7. CATEGORIZE → sınıflandır (önbellek/pinned, 06); etkilenen kategori catalog/README yenile
   8. EMBED      → object card + (gerekirse) chunk; dense+sparse (07)
   9. INDEX      → transactional per-object upsert; silinenleri cascade temizle
  10. MANIFEST   → _manifest + _changelog güncelle (03)
C. reconcile job → disk↔Postgres drift kontrolü (01)
```

Maliyet kuralı: **değişmeyen nesne hiçbir LLM/embedding çağrısı görmez** (önbellek de, `09`). 2000 SP'de günlük tipik değişim birkaç nesne → birkaç object job, saniyeler/dakikalar.

## Job durumu, retry ve dayanıklılık

- **Run kaydı:** `runs(id, server, started, finished, changed, added, removed, errors, drift, pending_dbs, degraded)` — `02`'den degraded/pending de burada.
- **Idempotent + resume:** Per-object `state` (`extracted→…→indexed`, Postgres) sayesinde crash'te yarım job kaldığı yerden (bkz. `03`/`01`).
- **Job hata politikası (karar):** Job başarısızsa **2 retry + backoff**; hâlâ olmazsa **dead-letter**'a düşer + alarm; nesne `state='failed'` (`09` fail-listesi) → **aramaya katılmaz**, indeksten düşürülür; sistem diğer job'lara devam eder. (Bağlantı-seviyesi retry `02`'de; bu job-seviyesi.) Düzelince sonraki sync/`reindex` ile otomatik yeniden denenir.
- **Kilit:** Aynı `(server,db)` keşfi için coalescing + DB advisory lock; per-object job'lar `SKIP LOCKED` ile çakışmaz. **Tek istisna:** advisory lock yalnızca aynı kapsamın eş-keşfini engeller; sistemin geri kalanını **bloke etmez** (`20` kilitlenmeme değişmezi).
- **Concurrency:** Farklı sunucular/DB'ler paralel; aynı nesne tek worker. Worker-içi paralel `object_jobs` ve AI/MSSQL semaphore'ları `20`'de yönetilir; havuz dolunca kuyruğa alınır, kilitlenmez.

### Süreçler-arası sürüm uyumu (karar, `REVIEW-gap-analysis` 2.5)
API ve Worker ayrı deploy edilir (`01`/`12`); ortak Postgres şeması ve job kuyruğu üstünden konuşurlar.
Sürüm kayması (biri yeni, biri eski) job'ları bozmamalı:
- **Geriye-uyumlu migration sırası:** **önce şema (migration runner) → sonra worker → sonra api.**
  Şema her zaman en az iki ardışık kod sürümüyle uyumlu (additive değişiklik; alan silme bir
  sonraki sürüme ertelenir).
- **Job payload `version` alanı:** Her job `payload.version` taşır. Worker, **bilmediği (daha yeni)**
  bir versiyon görürse job'u **dead-letter etmez** → `held` durumuna alır (beklet) ve atlar; worker
  yükseltilince `held` job'lar yeniden işlenir. Böylece yanlış sıralı deploy veri/iş kaybetmez.
- **`meta.schema_version`** (disk) ve Postgres şema sürümü `01`/`03`'teki migrator ile birlikte yükselir.

## Reconciler ve full reindex tetikleyicileri

- **Reconciler:** her sync sonunda + periyodik; disk↔Postgres `(uid,hash)` karşılaştırır, drift'i hizalar ve raporlar (`01`).
- **Full reindex:** Embedding `model`/`dim` değişti (`07` damga) → ilgili server/db full re-embed; manuel `db-agent reindex`. **Servis kesilmez:** re-embed sırasında sorgu **eski aktif set** üzerinden çalışır; yeni set arka planda yeni damgayla dolar; kapsam tamamlanınca atomik **swap** (`07` "Re-embed sırasında servis" kararı). Yarı-bitmiş set hiçbir zaman sorguya girmez.
- **Model-bump re-enrich:** Enricher/categorizer **chat modeli** değişirse, mevcut özet/kategoriler eski modelle üretilmiş kalır. Önbellek (`09`) yeni model_id'de otomatik geçersizdir ama proaktif değildir. Karar: `db-agent reindex --scope enrich` ile (veya config'te `reenrich_on_model_change: true`) etkilenen kapsam **yeniden enrich** edilir; varsayılan kapalı (maliyet), bilinçli tetiklenir.
- **Taksonomi tazeleme (karar):** Her sync'te değil — **periyodik + eşik tetikli** (`diger` oranı / yeni nesne sayısı eşiği aşılınca) `taxonomy` job'u kuyruğa (06).

## CLI komutları (sync tarafı)

```
db-agent worker                                # worker process'i başlat (kuyruğu tüketir)
db-agent discover --server kasko-sql           # keşif job'u kuyruğa
db-agent sync     [--server X] [--database Y]  # sync job(lar)ı kuyruğa  (--inline: o anda çalıştır)
db-agent reindex  --server X [--scope all]     # zorla yeniden işle
db-agent jobs     [--state pending|failed|dead] # kuyruk durumu / dead-letter
db-agent status                                # son run'lar, değişim, drift, pending DB
db-agent schedule list|run-now                 # zamanlanmış job'ları gör/çalıştır
```

## Decommission (config'ten server/db çıkarma)

Bir sunucu/DB config'ten çıkarılırsa kaybolan envanter "silindi" sanılıp kazara temizlenmemeli (soft-delete güvenliği keşfe bakar, config'e değil). Bunun yerine **açık decommission**:
- `db-agent decommission --server X [--database Y]` → ilgili disk store + Postgres kayıtları (objects/embeddings/edges) + kategori katalogları purge edilir, run-store'a `decommissioned` olayı yazılır.
- Config'ten sessizce çıkarma → sistem o kapsamı sadece **güncellemeyi durdurur** (var olan veri kalır, "stale" işaretlenir); kalıcı silme yalnızca açık komutla. Bu, yanlışlıkla config düzenlemesinin veri kaybına yol açmasını engeller.

## İzleme / bildirim (opsiyonel)

- Run özetini log + webhook (Slack/e-posta): "KaskoDB: 4 değişti, 1 eklendi, 0 hata, drift yok."
- Bildirim olayları: tamamlanma, **dead-letter**, drift, **pending (onay bekleyen) DB**, **yeni-kategori önerisi** (06), degraded sunucu (02).
- Sistem çalışmaya devam eder; sorunlu nesne `*_error`/dead-letter'da kalır, geri kalanı etkilemez.
