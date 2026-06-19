# 20 — Eşzamanlılık, Kapasite ve Performans

## Amaç

Sistemin **çok-kullanıcılı**, **paralel** ve **kilitlenmez** çalışmasını garanti etmek;
yapay zeka instance'larını ve token kullanımını kontrollü yönetmek; kapasiteye yaklaşıldığında
kullanıcıyı bilgilendirmek; ve **kullanıcı-tetikli performans testi** ile sistemin kaç eşzamanlı
kullanıcı/sohbet kaldırdığını ölçmek.

> Bu doküman `01` (topoloji), `09` (provider), `11` (scheduling), `12` (serving),
> `16` (observability), `17` (chat) ile birlikte okunur. Karar: **havuz-tabanlı eşzamanlılık
> + kuyruk-tabanlı sırtbasınç (backpressure) + kapasite sinyali + perf-test harness.**

## Temel ilke — sistem kilitlenmez

**Değişmez (invariant):** Hiçbir tek kullanıcı, tek ağır job veya tek yavaş provider
çağrısı tüm sistemi bloke edemez. Tek istisna: **keşif (discovery)** sırasında bir
`(server, database)` üzerinde alınan advisory lock — o da yalnızca aynı kapsamın eşzamanlı
keşfini engeller (`11` coalescing), başka hiçbir şeyi durdurmaz.

Bunu sağlayan üç ayrım:
- **Query-time ≠ indexing-time:** ayrı process (`01`); ağır indexing, sorgu gecikmesini hiç etkilemez.
- **Sınırlı havuzlar:** her paylaşılan kaynağın (kaynak MSSQL, AI instance, Postgres bağlantısı)
  **sınırlı eşzamanlılık havuzu** vardır; havuz dolunca **kuyruğa alınır**, sistem bloke olmaz.
- **Timeout + iptal:** her dış çağrının (LLM, embedding, MSSQL, rerank) duvar-saati timeout'u
  vardır; süre aşımı çağrıyı iptal eder, kaynağı havuza geri verir (sızıntı/kilit yok).

## Eşzamanlılık havuzları (concurrency pools)

Her paylaşılan kaynak ayrı, **config'ten ayarlanabilir** bir semaphore ile sınırlanır:

```yaml
# config/concurrency.yaml (veya servers.yaml altında)
concurrency:
  source_mssql:
    per_server: 2            # aynı sunucuya eşzamanlı bağlantı (prod'u yorma, 01 backpressure)
    across_servers: 8        # farklı sunucular paralel
  ai:
    chat_instances: 1        # eşzamanlı chat-LLM çağrısı (lokal vLLM slot sayısı)
    embed_batch_slots: 2     # eşzamanlı embedding batch
    rerank_slots: 1          # eşzamanlı rerank
  postgres:
    pool_min: 4
    pool_max: 20             # asyncpg engine havuzu (12 API + worker ayrı)
  worker:
    object_jobs: 4           # aynı worker içinde paralel per-object job (11)
  query:
    max_concurrent_chats: 16 # eşzamanlı aktif /ask oturumu (B)
    queue_depth: 32          # kuyrukta bekleyebilecek ek istek; aşılırsa "kapasite" sinyali
```

- **Indexing havuzları** (`source_mssql`, `embed`, `worker.object_jobs`) worker'a aittir;
  ağır ama sorgu yolundan izole.
- **Query havuzları** (`ai.chat_instances`, `query.max_concurrent_chats`) API'ye aittir;
  kullanıcı deneyimini belirler.

### Havuzlar kapasiteden türetilir — sabit değil (karar)
Eşzamanlı kullanıcı sayısı **sabitlenmez; sistem kapasitesine göre ölçeklenir.** Havuz değerleri
elle de verilebilir ama **varsayılan `auto`**:
- **`doctor` (`19`) donanımı algılar** (GPU sayısı/VRAM, CPU çekirdek, Postgres `max_connections`)
  ve havuzlara **makul başlangıç değerleri** koyar — ör. `ai.chat_instances` = vLLM'in VRAM/KV
  cache'ine sığan slot, `query.max_concurrent_chats` = bunun bir üst katı (kuyrukla).
- **`perf-test` (aşağıda) ile rafine edilir:** ölçülen doygunluk noktasının **~%70-80'i** hedef
  alınır (headroom). Operatör isterse değerleri sabitler.
- **Çalışma-zamanı uyum:** Donanım büyürse (daha çok VRAM/GPU, profil yükseldi → `09`) `auto`
  havuzlar yukarı ayarlanır; kapasiteye ulaşılınca yeni istek **reddedilmez, kuyruğa/`capacity`
  sinyaline** düşer (aşağıda) — yani sistem her zaman kapasitesi kadarını kaldırır, fazlasını
  zarifçe geri çevirir. Donanım eklenince kapasite kendiliğinden artar.

> Yani "kaç kişi" sorusunun cevabı config'te bir sayı değil; **mevcut kapasite** + `auto` havuzlar
> + kapasite sinyali. Net rakam `perf-test` çıktısıdır (donanım/modele özgü, her değişimde yeniden ölçülür).

## AI instance yönetimi

Yapay zeka, sistemdeki en kıt ve en pahalı kaynak. Yönetimi:

### Lokal (vLLM/Ollama)
- **vLLM ayrı süreç** (`09`): kendi içinde **continuous batching** yapar; aynı anda birden
  fazla istek alabilir. Bizim `ai.chat_instances` semaphore'ımız, vLLM'e aynı anda kaç
  istek **göndereceğimizi** sınırlar (kuyruğu vLLM'e değil, kendi tarafımızda tutarız →
  öncelik/iptal kontrolü bizde).
- **Tek instance, paylaşımlı:** Embedder/reranker worker içinde tek instance, lazy-load (`09`);
  paralel batch'ler semaphore ile sıraya girer.
- **Rol ayrımı (`09` roles):** Ağır `agent`/`enricher` ile hafif `categorizer`/`query_intent`
  farklı (küçük) modele yönlendirilebilir → küçük rol büyük modeli bloke etmez.

### Cloud (Vertex/OpenAI/Anthropic)
- Provider başına **rate-limit + eşzamanlılık throttle** (`09`); kota aşımında retry/backoff
  veya kuyrukta erteleme.
- `allow_cloud` ve sunucu/DB-bazlı lokal-zorunlu kısıtına saygılı (`09`/`14`).

### Indexing vs query önceliği
Aynı AI kaynağını hem indexing (enrich) hem query (agent) kullanabilir. Karar: **query önceliklidir.**
- Worker'ın enrich çağrıları **düşük öncelikli** slot kullanır; bir kullanıcı sorusu geldiğinde
  query, AI havuzunda öne geçer. Böylece "güncellenirken bile hızlı cevap ver" korunur.
- Lokal-tek-GPU senaryosunda chat ve enrich aynı vLLM'i paylaşırsa, enrich batch boyutu
  küçültülür / query saatlerinde enrich kısılır (config `enrich_offpeak`).

## Token yönetimi (3.2 — USD bütçe değil, token ölçümü)

Karar: **Maliyet odaklı USD tavanı yerine token-merkezli ölçüm ve kontrol.** (USD tavanı
`19`'da opsiyonel kalır; birincil sinyal token.)

Her LLM çağrısı için ölçülen ve `16`'ya akan değerler:
- `prompt_tokens`, `completion_tokens`, `total_tokens` (rol/provider kırılımı).
- **Bağlam doluluğu:** `context_used / context_window` (ör. %72) — modelin penceresine ne
  kadar yaklaşıldığı. Agent çok-turlu sohbette ve büyük nesne özetinde kritik.
- **Oturum/istek başı kümülatif token** (`17` `chat_messages.tokens` zaten var → toplanır).

Kontroller:
- **Token bütçeleme (`09`):** aday listeleri/kartlar/geçmiş, modelin `caps.max_context`'ine
  göre kırpılır; bağlam doluluğu eşiğe yaklaşınca önce semantik-geri-çağrılanlar, sonra
  pencere kırpılır, rolling-summary korunur (`17`).
- **Bağlam-dolu uyarısı:** Bir oturumda bağlam sürekli eşiğin üstündeyse (uzun sohbet),
  kullanıcıya "bu sohbet uzadı, özetleyip devam ediyorum / yeni oturum açabilirsin" sinyali.
- **Aşırı büyük nesne:** map-reduce özetleme (`09`) zaten token patlamasını engeller.

## Çok-kullanıcılı eşzamanlı sohbet + kapasite sinyali (B)

- **İzolasyon (`17`):** Her oturum `user_key` + `session_id` ile bağımsız; durum Postgres'te,
  agent state sorgu-bazlı in-memory (`10`) → oturumlar birbirini etkilemez.
- **Eşzamanlı `/ask`:** `query.max_concurrent_chats` slot'u kadar aktif sohbet paralel işlenir.
  Slot dolunca yeni istek `query.queue_depth` kadar **kuyruğa** alınır.
- **Kapasite sinyali (kilitlenme yerine bilgilendirme):**
  - Kuyruğa alınan istekte SSE `understanding`'den **önce** bir `queued` olayı yayılır:
    `{position, est_wait_ms}` → UI "sistem yoğun, sıradasın (≈Xs)" gösterir.
  - `queue_depth` de aşılırsa istek **reddedilmez-ama-ertelenir** değil; `503` + `Retry-After`
    + tipli `capacity` olayı döner: "şu an kapasite dolu, birazdan tekrar dene." (Sessiz
    bloke/timeout değil — açık sinyal.)
  - Eşik durumları `16`'ya metrik (`chats_active`, `chats_queued`, `capacity_rejections_total`)
    ve alarm olarak gider.
- **Adil paylaşım:** Tek kullanıcının çok sayıda eşzamanlı isteği diğerlerini aç bırakmasın
  diye `user_key` başına yumuşak eşzamanlılık limiti (`12` rate-limit middleware ile).

## Performans testi (C — kullanıcı tetikli)

Karar: **dahili yük-test harness'i**, yalnızca **operatör tarafından** tetiklenir
(prod'a karşı değil, hedef ortamda kapasite ölçümü için).

```
db-agent perf-test \
  --scenario chat|search|mixed \
  --concurrency 1,2,4,8,16,32 \      # artan eşzamanlılık basamakları
  --duration 60s \                    # her basamak süresi
  --queries-file perf/queries.yaml \  # gerçekçi soru seti (altın setten türetilebilir, 15)
  --target local|staging              # asla canlı prod kaynağa indexing yük bindirmez
```

Ölçülen ve raporlanan:
- **Throughput:** saniyede tamamlanan sorgu (chat & search ayrı).
- **Latency:** p50/p95/p99 (her eşzamanlılık basamağında).
- **Doygunluk noktası:** latency p95'in eşiği aştığı / kuyruk büyümeye başladığı eşzamanlılık
  → "sistem ~N eşzamanlı sohbet / ~M eşzamanlı arama kaldırıyor" çıktısı.
- **Kaynak korelasyonu (1.5):** her basamakta GPU/CPU/Postgres havuz doluluğu + token/s →
  darboğazın **hangi kaynak** olduğu (AI mi, Postgres mi, MSSQL mi) raporda gösterilir.
- Çıktı: terminal özeti + `perf/report-<ts>.json` + (varsa) Grafana'ya push.

Bu, kapasite planlamasını **tahminden ölçüme** taşır: deploy öncesi "bu donanım kaç kişi
kaldırır" sorusu deneyle yanıtlanır; havuz değerleri (`concurrency.yaml`) buna göre ayarlanır.

## Kaynak takibi ve yük-kaynağı bildirimi (1.5)

Sistem **sürekli** kaynak kullanımını izler (`16`):
- GPU/VRAM, CPU, Postgres havuz doluluğu, AI semaphore bekleme süresi, MSSQL havuz doluluğu,
  kuyruk derinliği, token/s.
- **Yük artışı kök-neden bildirimi:** latency veya kuyruk eşiği aşıldığında alarm yalnızca
  "yavaş" demez; **hangi kaynağın** doygun olduğunu söyler — ör. "p95 arama gecikmesi arttı;
  neden: AI rerank slot'u %100 dolu (embed batch ile çekişme)" veya "Postgres havuzu tükendi."
  Bu, `16` dashboard + webhook ile operatöre gider.
- Ölçek büyürse (çok DB/çok nesne) bu sinyaller **erken uyarı** verir; karar gereği şimdilik
  büyük-ölçek çözümü (sharding/ayrı vektör DB) **kurulmaz** — gerektiğinde, bu sinyallere
  dayanarak ileride eklenir (`13` ölçek notu).

## Diğer dokümanlarla bağ
- Havuz/timeout/iptal değişmezi: `01` (mimari değişmezler).
- AI instance + cost/rate-limit + token bütçe: `09`.
- Job paralelliği + `SKIP LOCKED` + advisory lock + coalescing: `11`.
- Eşzamanlı oturum + kapasite SSE olayı (`queued`/`capacity`): `12`, `18`.
- Token/kaynak metrikleri + kök-neden alarmı: `16`.
- Çok-kullanıcı izolasyon + bellek: `17`.
- Perf-test sorgu seti altın setten türetilir: `15`.
