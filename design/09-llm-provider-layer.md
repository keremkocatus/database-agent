# 09 — LLM Provider Katmanı

## Amaç

LLM ve embedding erişimini **tek bir ince soyutlamanın** arkasına almak; lokal GPU (vLLM/Ollama) veya cloud (Vertex/OpenAI/Anthropic) — ne varsa onunla çalışmak. Karar: **ince custom adapter** (framework/LiteLLM değil, kendi yazdığımız küçük katman).

## Neden custom adapter?

- **İstenen:** "Her şeye uyan bir katman, hangisi varsa onunla çalışsın."
- **Alternatif — LiteLLM:** 100+ provider hazır gelir; cazip. Ama bir bağımlılık + kendi soyutlama varsayımları + sürüm kırılmaları. Tam kontrol felsefesine ek yük.
- **Alternatif — framework-native (LangChain):** Orchestration framework'üne bağlar; biz custom ReAct seçtik (`10`), çelişir.
- **Seçim:** Birkaç yüz satırlık kendi adapter'ımız. İhtiyacımız dar (chat completion + tool-call + embedding); geniş bir kütüphaneye gerek yok. Yeni provider = yeni küçük sınıf.

## Arayüz (sözleşme)

```python
class LLMProvider(Protocol):
    def chat(self, messages: list[Msg], tools: list[ToolSpec] | None = None,
             schema: JsonSchema | None = None,     # structured output (aşağıda)
             temperature: float = 0.0, seed: int | None = None,
             max_tokens: int = 1024) -> LLMResponse: ...
    # LLMResponse: text | tool_calls[] | parsed(json)   (tek normalize şema)
    @property
    def caps(self) -> Caps: ...   # tool_calling, json_mode, max_context, ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], kind="passage") -> list[EmbedResult]: ...
    # EmbedResult: dense: Vector, sparse: SparseVec | None
    @property
    def dim(self) -> int: ...
    @property
    def supports_sparse(self) -> bool: ...   # BGE-M3 True, Vertex/OpenAI False
    @property
    def model_id(self) -> str: ...           # indeks damgası (07)

class RerankerProvider(Protocol):            # KARAR: reranker da soyut
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[Scored]: ...
    @property
    def model_id(self) -> str: ...
```

Tüm sistem **sadece bu protokolleri** tanır. Agent/enricher/categorizer/query_intent/cluster_labeler → `LLMProvider`; embedder → `EmbeddingProvider`; retrieval reranker → `RerankerProvider`. Seçim config'te.

- **Sparse capability:** `supports_sparse=False` ise (cloud embedding) retrieval otomatik `dense + trigram`'a düşer (`08`); sparse arm devre dışı. Sistem yine çalışır, lexical kalite biraz azalır.

## Desteklenen provider'lar (başlangıç)

| Provider | Chat | Embedding | Not |
|---|---|---|---|
| **vLLM (lokal)** | ✔ (OpenAI-uyumlu server) | — | GPU'da Gemma/Qwen; tool-call OpenAI formatı |
| **Ollama (lokal)** | ✔ | ✔ | Kolay kurulum, CPU/GPU; küçük modeller |
| **Vertex AI** | ✔ (Gemini) | ✔ (text-embedding) | Cloud; kurumsal |
| **OpenAI** | ✔ | ✔ | Cloud |
| **Anthropic** | ✔ (Claude) | — | Cloud; embedding yok (embed başka provider'dan) |
| **BGE-M3 (lokal)** | — | ✔ (dense+sparse) | Varsayılan embedding (07), FlagEmbedding |
| **bge-reranker (lokal)** | — | — | Varsayılan reranker (`RerankerProvider`, 08) |
| **Cohere/Vertex rerank** | — | — | Opsiyonel cloud reranker |

> Chat, embedding ve reranker provider'ları **bağımsız** seçilebilir: ör. chat=Anthropic (cloud reasoning), embedding=BGE-M3 (lokal), rerank=bge-reranker (lokal). "Hangisi varsa onunla çalış" esnekliği.

## Config

```yaml
llm:
  chat:
    provider: "vllm"                 # vllm | ollama | vertex | openai | anthropic
    base_url: "http://localhost:8000/v1"
    model: "Qwen/Qwen2.5-14B-Instruct"   # Apache-2.0 (varsayılan); gemma vb. opsiyon
    api_key_env: null                # cloud için .env değişken adı
    temperature: 0.0
  # rol-bazlı override (opsiyonel): ağır işe büyük model, hafif işe küçük
  roles:
    categorizer:    { provider: "ollama", model: "qwen2.5:3b" }   # ucuz/hızlı
    query_intent:   { provider: "ollama", model: "qwen2.5:3b" }   # ucuz/hızlı (08)
    cluster_labeler:{ provider: "ollama", model: "qwen2.5:3b" }   # taksonomi etiketi (06)
    enricher:       { provider: "vllm",   model: "Qwen/Qwen2.5-14B-Instruct" }
    agent:          { provider: "vllm",   model: "Qwen/Qwen2.5-14B-Instruct" }
  cache:
    offline_tasks: true        # categorizer/enricher: (prompt-hash+model) önbelleği
  structured_output: "schema"  # schema | prompt   (aşağıda)

embedding:
  provider: "local"
  model: "bge-m3"
  dim: 1024

reranker:
  provider: "local"            # local | cohere | vertex
  model: "bge-reranker-v2-m3"
```

### Rol-bazlı model (tek model, çoklu rol)
Önceki analizde karar: **tek model, çoklu rol** — ama gerekirse rol bazında override. Aynı vLLM instance'ı farklı system prompt'larla; prefix caching ortak kısmı bir kez hesaplar. İstenirse `categorizer` gibi hafif roller daha küçük/ucuz modele yönlendirilir. Bu, adapter'da `provider_for(role)` ile çözülür.

## Yapılandırılmış çıktı (structured output)

Karar: **JSON-schema/function-calling + doğrula/retry.** Categorizer (kategori enum), enricher (JSON özet), query_intent (etiket) gibi görevlerde:
- Provider native JSON-mode/function-calling destekliyorsa (`caps.json_mode`) onunla şema dayatılır.
- Her durumda çıktı **şemaya karşı doğrulanır** (pydantic); geçersizse 1-2 **retry** (gerekirse "yalnızca JSON döndür" hatırlatmasıyla).
- Hâlâ başarısızsa görev güvenli varsayılana düşer (categorizer → `diger`, düşük güven) ve log'lanır.
- Bu, heterojen provider'larda (zayıf lokal model dahil) güvenilir yapısal çıktı verir.

## Tool-calling: native + text-ReAct fallback

Karar: **native varsa native, yoksa text-ReAct fallback.**
- `caps.tool_calling=True` (vLLM tool-call, Claude, GPT) → native tool-call; adapter farklı formatları tek şemaya normalize eder (agent tek format görür, `10`).
- Native yoksa/zayıfsa → adapter, araçları prompt'a yazıp tool çağrılarını **metinden parse eden** ReAct fallback'ı kullanır. Böylece küçük/lokal modeller de agent olarak çalışır.

## Önbellek (offline görevler)

Karar: **(prompt-hash + model_id) yanıt önbelleği** (`cache.offline_tasks`). Categorizer/enricher gibi (temp 0) offline görevlerde aynı girdi → önbellekten yanıt. Reindex/yeniden çalışmada cloud maliyetini ve süreyi keser.

> **Determinizm notu (`REVIEW-gap-analysis` 3.4):** "temp 0 + seed" çıktıyı **garanti olarak**
> deterministik kılmaz — vLLM batching/sürüm farkları bit-aynılığı bozabilir. Doğru ifade:
> önbellek sayesinde aynı girdi **tekrarlanabilir** (önbellekten döner); ham model çağrısının
> bit-aynılığı **iddia edilmez**. "Deterministik" kelimesi dokümanlarda bu anlamda okunmalı.

## Dayanıklılık (resilience)

Adapter ince ama şunları içerir:
- **Retry + backoff:** geçici hata/timeout için **2 deneme** (üstel backoff).
- **Fallback zinciri:** `agent` provider'ı düşerse config'teki yedeğe geç (ör. lokal vLLM down → Vertex) — **`allow_cloud`'a saygılı** (kapalıysa cloud yedeğe geçmez).
- **Token bütçeleme:** her modelin `caps.max_context`'i bilinir; aday listeleri/kartlar buna göre kırpılır.
- **Eşzamanlılık + rate-limit:** provider başına `ai.*` semaphore (`20`) + kota throttle (`01` backpressure); slot dolunca **kuyruğa alınır, bloke etmez**. Token sayımı `16`'ya akar.
- **Tool-call normalizasyonu:** yukarıda (tek şema).
- **Timeout + iptal:** her çağrının duvar-saati timeout'u; aşımda iptal → slot havuza döner (sızıntı/kilit yok, `20`).

### Kalıcı başarısızlık → fail-listesi (karar, `REVIEW-gap-analysis` 3.4)
Bir **offline pipeline görevi** (enrich/categorize/embed) 2 retry sonrası hâlâ başarısızsa
(provider hatası, şema-doğrulama başarısız, parse_error), nesne sonsuz döngüye **sokulmaz**:
- Nesne `state = 'failed'` + `fail_reason` ile **ayrı bir başarısızlar listesine** alınır
  (`objects.state` + run-store; job tarafı dead-letter, `11`).
- **Aramaya katılmaz:** `failed` nesne indekse girmez / indeksten düşürülür → bozuk/yarım kayıt
  sorguya sızmaz. (Yapısal-only kart üretilebiliyorsa o tercih edilir, `07`; üretilemiyorsa hiç embed edilmez.)
- **Görünürlük:** `db-agent jobs --state failed` + `16` metrik/alarm; bir sonraki sync'te veya
  manuel `reindex` ile yeniden denenir. Geçici sorun düzelince otomatik geri kazanılır.
- Diğer nesneler etkilenmez (`11` "sistem diğer job'lara devam eder").

## Model lisansı (açık kaynak notu)

Sistem açık kaynak olacağı için **varsayılan modeller izin-dostu lisanslı** seçilir:
- **Varsayılan chat/enricher:** **Qwen2.5 (Apache-2.0)** — ticari/dağıtım serbest. (Örneklerde `gemma-3-12b` geçse de Gemma kendi lisansıyla gelir, kullanım kısıtları vardır → varsayılan değil, opsiyon.)
- **Embedding:** BGE-M3 (MIT), **reranker:** bge-reranker (Apache/MIT).
- Lisansı kısıtlı modeller (Gemma vb.) config'le seçilebilir ama varsayılan değildir; README model lisanslarını listeler.

## Aşırı büyük nesnenin özetlenmesi (map-reduce)

Bir nesnenin ham SQL'i model context'ini aşıyorsa (binlerce satır), enricher **hiyerarşik (map-reduce)** özetler: gövde mantıksal bloklara bölünür (sqlglot, `04`) → her blok kısa özetlenir → blok özetleri tek nesne özetine indirgenir. Token bütçesi (aşağıda) aşılmaz; çok büyük nesnede bile tutarlı `summary` üretilir.

## Kaynak yönetimi (GPU)

Worker (`11`) aynı anda **BGE-M3 embedder + bge-reranker + (lokal ise) chat LLM** yükleyebilir → tek GPU'da bellek çekişmesi.
- **vLLM ayrı süreç/sunucu:** Chat LLM genelde kendi vLLM server'ında; worker ona HTTP ile bağlanır (bellek izole). Embedder/reranker worker içinde (daha küçük).
- **Lazy load + tek instance:** Modeller ilk kullanımda yüklenir, süreç ömrü boyunca tek instance (paylaşılır), tekrar tekrar yüklenmez.
- **VRAM bütçesi:** config'te model başına tahmini VRAM; toplam GPU'yu aşarsa ya ayrı node'a dağıt (`01` worker ayrık) ya da daha küçük quantization seç.
- **CPU fallback:** GPU yoksa embedder/reranker CPU'da (yavaş ama çalışır); chat için Ollama/cloud.
- **Batch:** embedding/rerank batch'lenir (`07`/`08`) → GPU verimliliği.

### Donanım profili — 24 GB taban, yukarı otomatik ölçeklen (karar)
GPU **zorunlu değil** (cloud veya CPU de çalışır), ama lokal senaryoda **en kötü durum tek 24 GB GPU**'da
sorunsuz çalışacak şekilde ayarlanır; daha fazlası varsa **rahatça** kullanılır. 24 GB için özel kasıntı yok.

- **Taban profili (floor, tek 24 GB):** chat = **Qwen2.5-14B-Instruct AWQ (4-bit)** (~9 GB) + BGE-M3 (~2 GB)
  + bge-reranker (~2 GB) + KV cache. Bu iş yükünde (özet/kategori/kısa-bağlam ReAct) AWQ kalite kaybı ihmal edilebilir.
- **Otomatik ölçeklenme:** `doctor` (`19`) mevcut VRAM/GPU sayısını **algılar** ve profili ona göre seçer:
  - **>24 GB / 48 GB:** daha yüksek hassasiyet (AWQ yerine FP16/8-bit), daha büyük KV cache → daha çok
    eşzamanlı chat slot'u (`20`).
  - **2+ GPU:** chat (vLLM) ayrı GPU'da, embed/rerank worker GPU'sunda (`01`/`20` çekişme ayrımı).
  - **<24 GB / CPU:** Qwen2.5-**7B** kuantize (chat) + CPU embedder/reranker (yavaş ama çalışır) — fallback profili.
- **Cloud birinci-sınıf:** Donanım hiç yoksa chat/embedding cloud provider'dan (`allow_cloud` ile);
  hassas DB'ler yine lokal-zorunlu kalabilir (`14`). Provider seçimi config; çekirdek değişmez.
- **Damga:** Seçilen profil (model + quantization + dim) indekse damgalanır (`07`); embedding modeli/dim
  değişirse eski-set servis + swap devreye girer (`07`/`11`). Profil değişimi otomatik değil, bilinçli.

Profiller config'te adlandırılır (`hardware_profile: auto | gpu24 | gpu48 | multi_gpu | cpu | cloud`);
`auto` (varsayılan) doctor'ın algıladığını kullanır.

## Gizlilik anahtarı

Config'te `allow_cloud: false` ise sistem cloud provider'a **hiç** çağrı yapmaz (yanlış config'e karşı sigorta). Sunucu/DB bazında da kısıtlanabilir: bazı hassas DB'ler sadece lokal model kullanır. Bu, "mindset değişti, cloud da olsun" esnekliğini gizlilik kontrolüyle dengeler.
