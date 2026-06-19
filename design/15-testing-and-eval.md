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

### Altın set (karar — başlangıç noktası, otorite DEĞİL — 2.1)
- **Soru → beklenen `uid`(ler)** çiftleri. **Bootstrap:** M5'te elde set yokken **20–50 elle yazılmış**
  tohum çift ile başlar (eşik kalibrasyonu bununla; `08-E`). Zamanla **onaylı aramalardan** (`18`
  `search_feedback`) büyür.
- **Kesin doğru kabul edilmez:** Feedback/altın set retrieval'da yalnızca **başlangıç ipucu**dur;
  tam arama (dense+sparse+rerank) **paralel** çalışır ve aday sette yoksa/eşiği geçmezse sistem
  hemen tam aramaya döner (`08` C.5). Bu yüzden "altın" set bir *yön* verir, *karar* vermez.
- Türler: ad-araması, kavram, veri/tablo, gezinme, çok-hop bağımlılık, "bulunamamalı" (negatif).
- Çok-dilli (TR/EN karışık) örnekler. (Sahibi `13` açık soru #5; M5 başlama koşulu = tohum set.)

### Metrikler
- **Retrieval:** `recall@k`, `MRR`, `nDCG`; negatiflerde "no_match" doğru mu (false-positive eşik kalibrasyonu, `08-E`).
- **Agent:** cevap doğruluğu (kaynak `uid` beklenenle örtüşüyor mu), grounding ihlali oranı (uydurma `uid`), netleştirme/abstain davranışı, ortalama tool çağrısı/latency.
- **Ablasyon:** reranker'lı/sız, kart-only/kart+chunk, sparse açık/kapalı, niyet-yönlendirme açık/kapalı → parametre seçimi bu ölçümlere dayanır.

### Determinizm/regresyon
- LLM olasılıksal; bu yüzden eval **eşik-tabanlı** (örn. recall@5 ≥ X) ve **trend** izlenir (sürüm/model değişiminde düşüş alarmı).
- Offline görev önbelleği (`09`) → categorizer/enricher **tekrarlanabilirliği** test edilir. (Not: ham model çıktısının bit-aynılığı garanti değil; "tekrarlanabilir" önbellek üzerinden anlaşılır — `09` determinizm notu.)

### Koreferans / sorgu-yeniden-yazma eval (karar — 2.4)
`understand` (`10`) tek çağrıda niyet + rewrite + koreferans + netleştirme kararını yapar; bu
kritik adım yanlış çözerse tüm tur **sessizce** bozulur. Bu yüzden ayrı mini-set:
- Çok-turlu örnekler: "onu/bunu/o SP" → beklenen `uid` çözümü doğru mu.
- Türkçe eşanlamlı/kısaltma genişletme doğruluğu.
- Düşük güvende rewrite'ın kullanıcıya gösterilmesi (`10`) tetikleniyor mu.
- Metrik: koreferans çözüm doğruluğu, yanlış-rewrite oranı; trend izlenir.

## Kapsamlı unit test beklentisi (karar — vurgulanır)
Her use-case ve her deterministik bileşen için unit test **zorunlu** (yalnızca "olur" değil):
parser, değişim tespiti, tablo sözlüğü, redaction-kaldırıldı→onun yerine `allow_cloud` guard
testi, RRF/eşik matematiği, enrichment kalite kapısı (`05`), kategori taksonomi göçü (`06`),
Türkçe ad normalizasyonu (`07`), fail-listesi davranışı (`09`), süreç-sürüm uyumu/`held` (`11`).
Hedef: deterministik katmanda yüksek kapsama, LLM'siz çalışan hızlı kapı.

## CI

- **Hızlı kapı (her PR):** lint + type (ruff/mypy) + unit + sözleşme testleri (mock provider, mock DB). LLM/GPU **gerektirmez** → hızlı, ücretsiz.
- **Entegrasyon (nightly/etiketli):** Docker MSSQL + Postgres testcontainers; uçtan uca LLM'siz pipeline.
- **Eval (manuel/nightly):** altın set; lokal küçük model veya ucuz cloud ile; metrik raporu artefakt olarak.
- **Güvenlik:** bilinen prompt-injection korpusu (`14`) CI'da koşar. (Redaction suite kaldırıldı — redaction yok, `14`; yerine `allow_cloud` guard'ın hassas kapsamı cloud'a göndermediği test edilir.)

## Performans / yük testi (karar — `20`)
Kapasite ölçümü için **kullanıcı-tetikli** `db-agent perf-test` (artan eşzamanlılık basamakları →
throughput, p50/p95/p99, doygunluk noktası, kaynak kök-neden). "Sistem ~N eşzamanlı sohbet / ~M
arama kaldırıyor" çıktısı havuz ayarına (`20` `concurrency.yaml`) temel olur. Prod kaynağa indexing
yükü bindirmez. Detay: `20`. Perf sorgu seti altın setten türetilir.

## Test edilebilirlik ilkeleri
- Use-case'ler infra'yı **port** üzerinden görür → her şey mock'lanabilir, LLM/GPU/DB olmadan iş mantığı test edilir.
- Deterministik katman (extract/parse/store/dictionary) LLM'den **tamamen ayrı** test edilir (`01` katman bağımsızlığı).
- Fixtures: anonimleştirilmiş gerçek SP korpusu (PII/secret temizlenmiş) regression için saklanır.
