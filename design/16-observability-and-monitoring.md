# 16 — Observability ve Monitoring

## Amaç

Sistemin her an **takip edilebilir** olması: ne çalışıyor, ne kadar sürüyor, nerede hata var, ne kadar maliyet, indeks ne kadar taze. Karar: **Prometheus + Grafana (metrik) + OpenTelemetry (trace) + yapılandırılmış JSON log.** Hepsi `01` platform katmanının parçası; opsiyonel ama varsayılan açık.

## Üç sinyal türü

### 1) Yapılandırılmış log (JSON)
- Her log satırı JSON; ortak korelasyon alanları: `trace_id`, `run_id`, `job_id`, `server`, `database`, `uid`, `level`, `event`.
- Hassas alanlar maskeli (şifre, secret — `14`); dışlanan nesne adları log'a düşmez.
- Seviyeler: DEBUG (verbose/agent adımları) → INFO (job/sorgu olayları) → WARN (degraded, retry) → ERROR (dead-letter, parse_error).
- Çıktı: stdout (container-dostu); istenirse Loki'ye (opsiyonel, `13` "tam yığın").

### 2) Metrikler (Prometheus)
`/metrics` endpoint (API + worker ayrı expose). Temel metrik aileleri:

| Alan | Metrik (örnek) | Tür |
|---|---|---|
| Keşif/sync | `sync_runs_total{server,result}`, `objects_changed_total`, `sync_duration_seconds` | counter/histogram |
| Kuyruk | `jobs_pending`, `jobs_inflight`, `jobs_dead_total`, `job_retries_total` | gauge/counter |
| Pipeline | `parse_errors_total`, `partial_parse_total`, `embed_batch_seconds` | counter/histogram |
| Embedding/LLM | `llm_calls_total{role,provider}`, `llm_tokens_total{dir}`, `llm_latency_seconds`, `llm_cost_usd` | counter/histogram |
| Retrieval | `search_latency_seconds`, `rerank_used_total`, `no_match_total`, `recall_at_k` (eval) | histogram |
| Agent | `agent_tool_calls`, `agent_iterations`, `grounding_rejections_total`, `clarify_total` | histogram/counter |
| Reconciler | `drift_items`, `reconcile_runs_total` | gauge/counter |
| Sağlık | `provider_up{provider}`, `db_up`, `index_freshness_seconds` (son sync'ten beri) | gauge |

### 3) Trace (OpenTelemetry)
- Uçtan uca span'ler: `/ask` → understand → search (dense/sparse/trgm/RRF/rerank) → agent tool çağrıları → LLM çağrıları → cevap.
- Indexing tarafı: `discover → object job → extract/parse/enrich/embed/index` span zinciri.
- Exporter: OTLP → Tempo/Jaeger (opsiyonel) veya yalnızca log-trace korelasyonu (`trace_id`).
- `--verbose` CLI (`12`) trace'i okunur biçimde basar.

## Dashboard'lar (Grafana, hazır gelir)

1. **Operasyon:** sync run'ları, son güncelleme tazeliği (server/db), kuyruk derinliği, dead-letter, degraded sunucular, pending DB.
2. **Kalite:** parse_error/partial oranı, drift, `no_match` oranı, recall@k trendi (eval, `15`).
3. **Maliyet/performans:** LLM token/maliyet (provider/rol kırılımı), latency p50/p95, embedding throughput, GPU kullanımı (varsa).
4. **Sohbet:** aktif oturum, mesaj/oturum, özetleme tetikleri, ortalama tool çağrısı (`17`).

## Alerting

- **Kritik:** DB/provider down, dead-letter > eşik, sync N saattir başarısız (index bayatlıyor), drift > eşik.
- **Uyarı:** degraded sunucu, `no_match` oranı ani artış (indeks/retrieval bozulması sinyali), LLM maliyet eşiği.
- **Bilgi:** pending DB (onay bekliyor), yeni-kategori önerisi (`06`), gömülü-secret bulundu (`14`).
- Kanal: webhook (Slack/e-posta, `11`) + Grafana alert.

## Run-store (Postgres, kalıcı denetim)

`runs` ve `job` tabloları (`01`/`11`) tarihsel kayıt: ne zaman, ne değişti, ne kadar sürdü, hata/drift. Grafana bunu da kaynak alabilir (Postgres datasource). `db-agent status` aynı veriyi terminalde özetler.

## Gizlilik & retention

- Loglar/trace `14` maskeleme kurallarına uyar; dışlanan nesne adları hiçbir sinyalde görünmez.
- Retention (`01`): run/trace/log saklama penceresi yapılandırılır; periyodik temizlik job'u (`11`).

## Tasarım notu
Metrik/trace, application use-case'lerine **dekoratör/middleware** ile eklenir (iş mantığına sızmaz, `01` platform katmanı). Provider adapter'ları (`09`) zaten token/latency/cost ölçer → metriklere doğrudan akar.
