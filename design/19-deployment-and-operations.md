# 19 — Deployment ve Operasyon

## Amaç

Sistemi sıfırdan ayağa kaldırma, ilk indeksleme (bootstrap), yedekleme/kurtarma ve operasyonel komutlar. Açık kaynak kullanıcısının "nasıl çalıştırırım"ının tek adresi.

## Dağıtım topolojisi

İki uzun-ömürlü process (`01`): **API** (`db-agent serve`) + **Worker** (`db-agent worker`), ortak Postgres. Lokal LLM kullanılıyorsa **vLLM/Ollama** ayrı servis. Observability için **Prometheus + Grafana** (`16`). Lokal modeller worker'a yakın (GPU node).

```
┌────────┐   ┌──────────┐   ┌─────────────┐   ┌──────────────┐
│  API   │   │  Worker  │   │ PostgreSQL  │   │ vLLM/Ollama  │
│ serve  │──▶│ (GPU)    │──▶│ + pgvector  │   │ (chat LLM)   │
└────────┘   └────┬─────┘   └─────────────┘   └──────▲───────┘
                  └───────── embed/rerank/LLM ───────┘
   Prometheus ◀ /metrics (API+Worker) ;  Grafana ◀ Prometheus + Postgres
```

### docker-compose (öneri, geliştirme/küçük-prod)
Servisler: `postgres` (pgvector+pg_trgm imajı), `api`, `worker`, opsiyonel `ollama`/`vllm`, opsiyonel `prometheus`+`grafana`. `api` ve `worker` aynı imajdan farklı komutla. ODBC sürücüsü imaja gömülür.

### ODBC (Linux)
Imaj: `msodbcsql18` + `unixODBC` kurulu. `config/servers.yaml` `driver: "ODBC Driver 18 for SQL Server"` (`02`). Kerberos yok (SQL auth, `02`).

### GPU
- vLLM ayrı container/host (chat LLM, kendi VRAM'i). Worker içinde BGE-M3 embedder + bge-reranker (küçük). VRAM bütçesi `09`.
- GPU yoksa: Ollama/cloud chat + CPU embedder/reranker (yavaş ama çalışır).

## İlk kurulum: `init` / `doctor`

```
db-agent init       # .env + config iskeleti üret, migration'ları uygula, pgvector/pg_trgm kur
db-agent doctor     # ön-uçuş kontrolü:
                    #  - config şema doğrula (pydantic), exclusion kuralları geçerli mi
                    #  - her sunucuya bağlan (read-only + ApplicationIntent) → erişim/yetki testi
                    #  - Postgres + extension + migration sürümü
                    #  - provider erişilebilirlik + capability probe (09): chat/embed/rerank
                    #  - GPU/VRAM/CPU algıla → hardware_profile seç (auto: gpu24|gpu48|multi_gpu|cpu|cloud, 09)
                    #  - auto havuz değerlerini kapasiteye göre öner (20); perf-test ile rafine edilir
                    # → yeşil/sarı/kırmızı rapor; kırmızı varsa ne yapılacağını söyler
```
`healthz` (`12`) çalışma-zamanı; `doctor` setup-zamanı doğrulamadır.

## Bootstrap (ilk indeksleme) — incremental'den farklı sıra

İlk çalıştırmada taksonomi henüz yok ve kümeleme **tüm korpusun embed'ini** gerektirir. Bu yüzden ilk-run özel sıralı:

```
1. discover (server/db)                     # envanter + exclusion filtresi (02/14)
2. her nesne: extract → parse → enrich → embed   # (categorize HENÜZ değil)
3. taxonomy job: embedding-kümeleme + etiketleme (06)   # korpus hazır olunca
4. categorize: tüm nesneleri taksonomiye eşle (06)
5. catalog/README üret (06) ; reconcile (01)
```
İncremental run'da (sonraki) taksonomi zaten var → sıra `06`'daki normal akış (her object job kendi içinde categorize eder).

### Faz faz (DB-by-DB) devreye alma — ilk DB biter bitmez sorgulanabilir (karar — 1.5)
Çok sunucu/DB'de hepsini birden değil, **DB-başına sırayla**: önce `db-agent sync --server X --database Y`
ile **tek DB** bootstrap → doğrula (search/ask dene) → sonraki DB. `discover_then_approve` (`02`) bunu
doğal kılar.
- **İlk-DB-sonrası sorgu:** Sorgulama, **ilk DB'nin bootstrap'ı biter bitmez** başlayabilir; tüm
  DB'lerin bitmesi beklenmez. Her DB bağımsız "hazır" olur (`object_kind`/scope ile izole).
- **Bitmemiş DB'den soru → uyarı:** Henüz hazır olmayan (bootstrap sürüyor / pending / onaysız) bir
  DB kapsamında soru gelirse agent sessiz boş dönmez: *"bu DB henüz katalogda hazır değil
  (indeksleniyor/onay bekliyor)"* der (`10`/`16` `index_freshness`). Hazır DB'ler normal cevaplanır.
- Beklenti: 2000 nesnelik bir DB'nin ilk embed'i GPU'da dakikalar; faz faz ilerleyince yük/maliyet kontrollü.
- **Ölçek:** Büyük korpusta kaynak takibi (`16`/`20`) yük kök-nedenini gösterir; büyük-ölçek özel
  çözümü (sharding vb.) baştan kurulmaz, gerekirse ileride (`13` ölçek notu).

## Yedekleme ve kurtarma (DR)

Otorite ayrımı (`01`) yedek kapsamını belirler:

| Veri | Otorite | Yeniden üretilebilir? | Yedek |
|---|---|---|---|
| Lokal disk store (tanım, meta, catalog, changelog) | disk | — (kaynaktan re-sync mümkün) | **Evet** (asıl yedek) |
| `config/servers.yaml` + `.env` | disk | hayır | **Evet** (secret ayrı/kasada) |
| Postgres **türetilmiş indeks** (objects/embeddings/edges) | Postgres | **Evet** (diskten `reindex`) | gerekmez (hız için opsiyonel) |
| Postgres **otoriter durum** (`search_feedback` 18, `chat_*` 17, `runs`/trace 16) | Postgres | **HAYIR** | **Evet — zorunlu** |

- **Yedek planı:** (a) disk store + config periyodik snapshot; (b) Postgres **otoriter tabloların** `pg_dump`'ı (feedback/chat/runs). Türetilmiş indeks istenirse dahil edilir ama diskten kurulabilir.
- **Kurtarma:** disk + config geri yükle → `reindex` (indeks diskten kurulur) → otoriter Postgres dump'ını geri yükle (feedback/chat/runs geri gelir). Böylece **hiçbir öğrenilmiş sinyal/sohbet kaybolmaz**.

> **Risk notu — ham SQL & geçmiş disk-only (`REVIEW-gap-analysis` 3.1):** Ham `.sql` ve `.prev.sql`
> **yalnızca disk store'da** tutulur (Postgres yalnızca yapısal `meta` JSONB + kart içeriği taşır,
> ham gövdeyi değil). Dolayısıyla **disk yedeği birincil ve kritiktir.** Senaryo: bir nesne kaynakta
> silindi **ve** disk yedeği de kayıp → o tanımın ham metni ve geçmişi **kalıcı kaybolur** (kaynaktan
> re-sync artık mümkün değil). Bu yüzden disk store snapshot'ı düzenli ve doğrulanmış olmalı.
> (İsteğe bağlı sağlamlaştırma: ham gövdeyi Postgres'e ikincil kopya olarak da yazmak — disk
> birincil otorite kalır; v1'de zorunlu değil, risk burada açıkça kabul edilir.)

## Kullanım izleme / kill-switch (token-merkezli — karar 3.2)

- **Birincil sinyal token** (USD değil): `16` token-merkezli ölçüm (prompt/completion/total + bağlam
  doluluğu). USD tavanı (`daily_usd_cap`/`monthly_usd_cap`) **opsiyonel** kalır, token sayımından türetilir.
- **Kill-switch:** İsteğe bağlı token/maliyet tavanı aşılınca yeni cloud çağrıları **durur**, sistem
  lokal modele düşer (varsa) ya da görevi kuyrukta erteler + alarm (`16`). Query-time `allow_cloud`'a saygılı.
- **Operatöre değer:** "AI ne kadar token kullanıyor, bağlam ne kadar doldu" doğrudan görünür (`16`/`20`);
  yük artışında kök-neden bildirimi gelir (`20`).

## Quickstart / demo

- `docker compose up` + `db-agent init` + seed bir demo MSSQL (`15` entegrasyon şeması) → 5 dakikada `db-agent ask "..."`.
- README'de adım adım; örnek `servers.example.yaml` + `.env.example`.

## Operasyon komutları (özet, `11`/`12` ile)
```
db-agent init | doctor
db-agent serve | worker
db-agent discover | sync [--inline] | reindex | decommission
db-agent jobs [--state pending|failed|dead|held] | status | sessions
db-agent backup [--scope disk|authoritative|all] | restore
db-agent perf-test --scenario chat|search|mixed --concurrency 1,2,4,8,16 --duration 60s   # kapasite ölçümü (20)
```
