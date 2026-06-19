# 00 — Genel Bakış

> Bu doküman serisi, projenin **tasarım kararlarını** kategori kategori kayıt altına alır.
> Her dosya bir karar kümesini, değerlendirilen alternatifleri ve seçim gerekçesini içerir.
> Kod yazımından önce "ne, neden, nasıl" bu dokümanlarda netleşir.

## Tek cümlede sistem

Kendisine **doğrudan bağlantı bilgisi verilen MSSQL sunucularını/veritabanlarını keşfeden**, içlerindeki tüm programlanabilir nesneleri (SP, View, Function, Trigger) ve tablo yapısını **belirli bir schedule'da lokale indiren**, bunları **agentic biçimde klasörleyip her klasör için metadata üreten**, **kendini sürekli güncelleyen** ve sorulduğunda **doğal dilde cevap veren** kendine yeten, lokal, açık kaynak bir sistem.

## Hedefler

1. **Keşif (Discovery):** Sadece sunucu bağlantısı verildiğinde içindeki database'leri, şemaları ve nesneleri otomatik bulmak.
2. **Senkronizasyon (Sync):** Programlanabilir nesneleri ve tablo şemalarını lokale indirmek; sadece değişenleri yeniden işlemek (incremental).
3. **Anlamlandırma (Enrichment):** Her nesne için yapısal metadata + LLM özeti; her tablo için data dictionary; bağımlılık grafiği.
4. **Organizasyon (Foldering):** Yapı + anlam hibrit klasörleme; her klasör için README + makine-okunur katalog.
5. **Arama (Retrieval):** Hybrid (vektör + keyword) + reranker ile "şu işi yapan nesne hangisi" sorusuna isabetli cevap.
6. **Sohbet (Agentic chat):** ReAct döngülü bir agent'ın araçları kullanarak çok-adımlı sorulara cevap vermesi.
7. **Otonomi (Self-update):** Schedule ile kendini güncelleyip indeksi tazeleyen, minimum müdahale gerektiren bir sistem.

## Kapsam dışı (şimdilik)

- Canlı veriyi okumak / sorgu sonucu döndürmek (sadece şema + tanım metadata; veri profilleme opsiyonel ve kapalı).
- SP'leri değiştirmek / deploy etmek (sistem **salt-okunur** kaynak DB'ye karşı).
- Mevcut GitHub SP-migration repo'su ile entegrasyon (bu sistem ondan **bağımsız**, git kullanmaz).

## Tasarım ilkeleri

- **Provider-agnostik:** LLM ve embedding katmanı; lokal GPU (vLLM/Ollama) veya cloud (Vertex/OpenAI/Anthropic) — ne varsa onunla çalışır.
- **Minimum bağımlılık, tam kontrol:** Ağır framework yok. Custom adapter + custom ReAct loop + tek DB (PostgreSQL/pgvector).
- **Deterministik önce, LLM sonra:** Extraction/parsing/şema tamamen deterministik; LLM sadece özet/kategori/açıklama gibi anlamsal adımlarda.
- **Incremental:** Her şey "sadece değişeni işle" mantığında. Full reindex pahalıdır, istisnadır.
- **Çok-kiracılı (multi-tenant):** Çok sunucu, çok DB. Her şey `server/database` ekseninde izole.
- **Salt-okunur kaynak:** Kaynak MSSQL'e asla yazma. Risksiz çalışma.
- **Dengeli öncelik:** Arama kalitesi, otonomi ve basitlik arasında denge; aşırı mühendislik yok.

## Çözülen ana kararlar (özet)

| Konu | Karar | Detay dosyası |
|---|---|---|
| Yazılım mimarisi | Clean Architecture (api/cli/worker · application · domain · infrastructure); AI = application/agent slice | `12`, `13` |
| Süreç topolojisi | API + Worker ayrık, Postgres job-queue; otorite alana-bölünmüş + reconciler | `01`, `11` |
| LLM/embedding/reranker hosting | Provider-agnostik (lokal + cloud), ince custom adapter (= infra port'ları) | `09` |
| Embedding modeli | BGE-M3 (dense+sparse, swappable) | `07` |
| Index | PostgreSQL: pgvector(dense+sparse) + pg_trgm + metadata + graph | `07`, `08` |
| DB erişimi | SQLAlchemy async engine + ham SQL; DB-tarafı RPC yok; SQL-dosya migration | `13` |
| Retrieval | dense+sparse+trigram → RRF+tam-ad boost → uyarlamalı cross-encoder rerank → eşik | `08` |
| Orchestration | Custom ReAct loop (framework yok); niyet+grounding doğrulama | `10` |
| Extraction kapsamı | SP+View+Function+Trigger + tablo/view sözlüğü + synonym/cross-DB/UDT | `02`,`03`,`05` |
| Değişim tespiti | modify_date + content hash; hibrit kimlik (uid+alias); changelog+prev | `03` |
| Scheduling | APScheduler enqueue → Worker (per-object job); retry→dead-letter | `11` |
| Klasörleme | Hibrit; **kod + veri ayrı taksonomi**; birincil+ikincil; pinned | `06` |
| Reranker | Cross-encoder (bge-reranker), swappable | `08`, `09` |
| Repo ilişkisi | Bağımsız araç, doğrudan DB keşfi, git yok | `02`, `03` |
| Güvenlik | Exclusion (çok-seviye+glob, tamamen görünmez) + per-user rol görünürlük + prompt-injection guardrail + scope auth; **redaction yok** (`allow_cloud`/provider seçimi) | `14` |
| Eşzamanlılık/Kapasite | Havuz-tabanlı paralellik + kuyruk (kilitlenmez) + AI instance + token yönetimi + çok-kullanıcı + perf-test | `20` |
| Observability | Prometheus + Grafana + OpenTelemetry + JSON log | `16` |
| Chatbot | Kalıcı oturum + pencere/rolling-summary/semantik bellek | `17` |
| Etkileşim | Uyarlamalı netleştirme (1-3) + bulgular/gerekçe/onay + onaydan öğrenme + tipli SSE streaming | `18` |
| Deployment/Operasyon | docker-compose + init/doctor + DB-by-DB bootstrap + yedek/restore (DR) + token-tabanlı kill-switch | `19` |
| Model lisansı | Açık-kaynak default Apache (Qwen/BGE); kısıtlı modeller opsiyon | `09` |
| Test/Eval | Unit/entegrasyon/sözleşme + retrieval altın seti + CI | `15` |
| Arayüz | FastAPI REST + Typer CLI + Worker | `12` |

## Sözlük

- **SP:** Stored Procedure.
- **Nesne (object):** Programlanabilir DB nesnesi — SP, View, Function, Trigger.
- **Object card:** Bir nesnenin embedding'i için üretilen özet kartı (ad + parametreler + tablolar + LLM özeti + kategori).
- **Data dictionary / tablo sözlüğü:** Tabloların kolon/tip/PK/FK + LLM açıklaması içeren yapısal sözlüğü.
- **Hybrid retrieval:** Dense vektör + BGE-M3 sparse (öğrenilmiş lexical) + pg_trgm (fuzzy ad) aramasının birleşimi.
- **RRF:** Reciprocal Rank Fusion — iki sıralı listeyi birleştiren skor füzyonu.
- **Reranker:** Aday sonuçları sorguya göre yeniden sıralayan cross-encoder.
- **ReAct:** Reason + Act — LLM'in düşün → araç çağır → gözlemle döngüsü.
- **Incremental sync:** Sadece değişen nesneleri yeniden işleme.
