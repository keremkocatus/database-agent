# 15 — Test ve Değerlendirme

## Amaç

İki ayrı kalite ekseni: **(A) doğruluk testleri** (kod beklendiği gibi mi çalışıyor — deterministik) ve **(B) retrieval/agent değerlendirmesi** (arama/cevap ne kadar isabetli — olasılıksal). Clean Architecture, test edilebilirliği kolaylaştırır: use-case'ler port'lara bağlıdır → infra mock'lanır.

## A) Doğruluk testleri (deterministik)

### Unit
- **Parser (`04`):** Örnek T-SQL parçaları → beklenen parametre/return/tablo/çağrı; UDT çözümleme; dinamik SQL `has_dynamic_sql`; `partial_parse` fallback; **regression korpusu** (gerçek SP'lerden anonimleştirilmiş zorlu örnekler).
- **Değişim tespiti (`03`):** modify_date+hash; rename = taşıma (uid sabit); soft-delete güvenliği (degraded'da silmeme); changelog/prev üretimi.
- **Tablo sözlüğü (`05`):** kolon/PK/FK/check/computed çıkarımı; extended-property önceliği.
- **Redaction (`14`):** secret kalıpları maskeleniyor mu; false-positive oranı.
- **Retrieval füzyonu (`08`):** RRF + tam-ad boost matematiği; eşik/`note` davranışı (sentetik skorlarla).
- **Use-case'ler:** port'lar mock'lanır (FakeLLM, FakeEmbedding, FakeSourceDb, InMemoryRepo); iş mantığı izole test.

### Entegrasyon
- **Örnek MSSQL:** Docker `mssql/server` + tohum şema (birkaç SP/View/Function/Trigger/tablo + synonym + cross-DB) → discover→extract→parse→store **uçtan uca** doğrulanır (LLM'siz kısım).
- **Postgres + pgvector:** geçici DB (testcontainers) + SQL migration runner → repository'ler (catalog/vector/graph/jobs) gerçek SQL'e karşı; HNSW/sparsevec/pg_trgm sorguları çalışıyor mu.
- **Job-queue (`11`):** enqueue → `SKIP LOCKED` dequeue → retry → dead-letter; coalescing; resume (state).
- **Reconciler (`01`):** kasıtlı disk↔Postgres drift → hizalama doğru mu.

### Sözleşme (contract) testleri
- Her **provider adapter** (`09`) aynı `LLMProvider`/`EmbeddingProvider`/`RerankerProvider` sözleşmesini geçmeli (tool-call normalize, JSON-schema, dense+sparse, capability bayrakları). Yeni provider eklemek = aynı suite'i geçmek.

## B) Retrieval & agent değerlendirmesi (olasılıksal)

### Altın set
- **Soru → beklenen `uid`(ler)** çiftleri. Kaynak: kurum uzmanı + gerçek kullanıcı sorularından örnekleme + **onaylı aramalardan** (`18` `search_feedback`, insan küratör onayıyla). (Sahibi `13` açık soru #5.)
- Türler: ad-araması, kavram, veri/tablo, gezinme, çok-hop bağımlılık, "bulunamamalı" (negatif).
- Çok-dilli (TR/EN karışık) örnekler.

### Metrikler
- **Retrieval:** `recall@k`, `MRR`, `nDCG`; negatiflerde "no_match" doğru mu (false-positive eşik kalibrasyonu, `08-E`).
- **Agent:** cevap doğruluğu (kaynak `uid` beklenenle örtüşüyor mu), grounding ihlali oranı (uydurma `uid`), netleştirme/abstain davranışı, ortalama tool çağrısı/latency.
- **Ablasyon:** reranker'lı/sız, kart-only/kart+chunk, sparse açık/kapalı, niyet-yönlendirme açık/kapalı → parametre seçimi bu ölçümlere dayanır.

### Determinizm/regresyon
- LLM olasılıksal; bu yüzden eval **eşik-tabanlı** (örn. recall@5 ≥ X) ve **trend** izlenir (sürüm/model değişiminde düşüş alarmı).
- Offline görev önbelleği (`09`) + temp 0 + seed → categorizer/enricher tekrarlanabilirliği test edilir.

## CI

- **Hızlı kapı (her PR):** lint + type (ruff/mypy) + unit + sözleşme testleri (mock provider, mock DB). LLM/GPU **gerektirmez** → hızlı, ücretsiz.
- **Entegrasyon (nightly/etiketli):** Docker MSSQL + Postgres testcontainers; uçtan uca LLM'siz pipeline.
- **Eval (manuel/nightly):** altın set; lokal küçük model veya ucuz cloud ile; metrik raporu artefakt olarak.
- **Güvenlik:** redaction suite + bilinen prompt-injection korpusu (`14`) CI'da koşar.

## Test edilebilirlik ilkeleri
- Use-case'ler infra'yı **port** üzerinden görür → her şey mock'lanabilir, LLM/GPU/DB olmadan iş mantığı test edilir.
- Deterministik katman (extract/parse/store/dictionary) LLM'den **tamamen ayrı** test edilir (`01` katman bağımsızlığı).
- Fixtures: anonimleştirilmiş gerçek SP korpusu (PII/secret temizlenmiş) regression için saklanır.
