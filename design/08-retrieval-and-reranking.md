# 08 — Retrieval ve Reranking

## Amaç

"Şu işi yapan nesne hangisi" sorusuna isabetli aday kümesi döndürmek. Karar: **niyet yönlendirme → dense+sparse hybrid (+tam-ad boost) → RRF → uyarlamalı cross-encoder rerank → eşik.**

## Boru hattı

```
sorgu
  │
  ▼
(0) NİYET sınıflandırma (LLM)   → name | capability | data | navigation
  │   (agent'tan gelirse sorgu-yeniden-yazma adımına katılır, ekstra çağrı yok; 10)
  │
  ├─▶ (A) Dense vektör (pgvector HNSW)     → top 30 (anlamsal)
  ├─▶ (B) Sparse vektör (BGE-M3 sparsevec) → top 30 (öğrenilmiş lexical)
  ├─▶ (N) Trigram tam/kısmi ad (pg_trgm)   → tam-ad BOOST / kısa-devre
  │
  ▼
(C) RRF(dense, sparse) + tam-ad boost  →  birleşik top ~40
  │
  ▼
(D) Uyarlamalı rerank (bge-reranker)   →  en iyi 5–10   (tam-ad baskınsa atlanır)
  │
  ▼
(E) Skor eşiği  →  yeterli mi? değilse "düşük güven / bulunamadı"
  │
  ▼
agent'a / kullanıcıya
```

Niyet, hangi havuzun öne çıkacağını belirler: `name`→trigram kısa-devre, `capability`→dense ağırlık, `data`→tablo/view kartları (`object_kind`), `navigation`→`kind='category'` kartları.

## (0) Niyet sınıflandırma (LLM)
Karar: **LLM ile niyet sınıflandırma.** Sorgu `name | capability | data | navigation` etiketlenir → boru hattı buna göre ağırlıklandırılır.
- **Agent üzerinden gelirse** (`/ask`): niyet, agent'ın ilk adımındaki sorgu-yeniden-yazma ile **aynı çağrıda** çıkar → ekstra LLM maliyeti yok (`10`).
- **Ham `/search`** (agent'sız): küçük/ucuz model rolüyle (`09` `categorizer` rolü) tek hızlı sınıflandırma.
- Belirsizse `capability` (en güvenli, dense ağırlıklı) varsayılır.

## (A) Dense + (B) Sparse arama
- **Dense:** sorgu BGE-M3 dense → cosine ANN (HNSW).
- **Sparse:** sorgu BGE-M3 sparse → `sparsevec` inner-product ANN (öğrenilmiş lexical; eski tsvector'ün yerine).
- `kind='card'`/`'table'`/`'category'` öncelikli; `kind='body'` sonuçları **nesne düzeyinde toplanır** (aynı nesne tek satır; skor = en iyi chunk).
- Kapsam filtresi opsiyonel: `server, database, object_kind, category, secondary_categories`, ve read/write (`04`).

## (N) Trigram tam/kısmi ad — boost
- `pg_trgm` ile ad benzerliği (`SP_TEKLF`, `teklif sure`). Vektör benzerliği ad-aramada zayıf kalır; bu onu kapatır.
- **Eşit RRF üyesi değil:** tam-ad eşleşmesi bir **boost** (ve `name` niyetinde **kısa-devre** → o nesne en üste). Karar gereği RRF çekirdeği dense+sparse, ad sinyali üstüne biner.

## (C.5) Öğrenilen geri-bildirim boost'u (opsiyonel)
Geçmiş **onaylı aramalar** (`18` `search_feedback`) varsa: yeni sorgu semantik olarak yakın bir onaylı sorguya benziyorsa, onun `confirmed_uids`'i RRF sonrası **hafif boost** alır (rejected'lar hafif ceza). Aynı `scope` (`14`) içinde, sınırlı + zamanla sönen — aşırı kilitlenmeyi önler. Dışlanan `uid` asla boost almaz.

## (C) RRF (Reciprocal Rank Fusion) + tam-ad boost
Dense ve sparse listelerini skaladan bağımsız birleştirir:
```
score(d) = Σ_{liste∈{dense,sparse}}  1 / (k + rank_liste(d))     # k ≈ 60
score(d) += name_boost(d)                                        # trigram/tam-ad
```
- RRF sadece sıraya bakar → cosine ile sparse-IP farklı ölçekte olsa da sorun yok.
- Çıktı: ~40 aday (rerank için bol aday).

## (D) Uyarlamalı cross-encoder reranker
Karar: **uyarlamalı rerank** (`bge-reranker-v2-m3`).
- **Atla:** `name` niyeti + tam-ad eşleşmesi baskınsa rerank gereksiz (sonuç zaten kesin) → gecikme yok.
- **Çalıştır:** `capability`/`data`/belirsiz sorgularda top-40 → (sorgu, kart) cross-encoder → en iyi 5–10. Body-chunk isabetinde rerank o chunk içeriği üzerinde.
- Lokal, BGE-M3 ailesi (dil uyumu), birkaç on ms ek gecikme.

## Dışlama (defense-in-depth)
Dışlanan nesneler zaten indekse hiç girmez (`02`/`14`), dolayısıyla aramada doğal olarak çıkmaz. Yine de retrieval, her sorguda hem **kullanıcı kapsamını** (scope, `14`) hem **aktif exclusion kuralını** zorunlu filtre olarak uygular — sonradan eklenmiş ama henüz purge edilmemiş bir kayıt bile sızmaz. Dışlanan `uid`'ler sonuç, `why` ve `note`'ta hiç yer almaz.

## (E) Skor eşiği — dürüst cevap
Karar: rerank/RRF skoru eşiğin altındaysa "kesin eşleşme yok, yakın adaylar şunlar" ya da "bulunamadı" döner. Agent uydurmaz; kullanıcı alakasız sonucu "cevap" sanmaz. Eşik altın set ile kalibre edilir (aşağıda).

## Retriever arayüzü (agent ve CLI ortak kullanır)

```python
search_objects(
    query: str,
    top_k: int = 8,
    server: str | None = None,
    database: str | None = None,
    object_kind: str | None = None,     # 'code' | 'table' | 'view'
    category: str | None = None,        # birincil/ikincil ile eşleşir
    types: list[str] | None = None,     # ['procedure','view',...]
    writes_table: str | None = None,    # sadece bu tabloyu YAZANlar (04)
    reads_table: str | None = None,     # sadece bu tabloyu OKUYANlar (04)
    intent: str | None = None,          # verilmezse (0)'da sınıflandırılır
) -> SearchResponse

# SearchResult: uid, alias, type, score, summary, uses_tables, category, why
# SearchResponse: results[], confidence, note ("low_confidence" | "no_match" | None)
```
- Agent'tan bağımsız çağrılabilir → CLI/`/search` "ham arama" modu (agent'sız).
- `why`: eşleşen lexical terim/kolon + niyet → debug + agent muhakemesi.
- `note`: eşik politikası sonucu (`08-E`); agent buna göre dürüst cevap kurar.

## Tablo/veri araması
Aynı boru hattı `object_kind in ('table','view')` üzerinde → `search_tables(query)`. "Müşteri iletişim verisi nerede" → ilgili tablo **ve view**'lar (`05`). `kind='category'` ile veri-alanı gezinme de mümkün.

## Kalite ölçümü (sonraki aşama)
- Küçük bir **altın set** (soru → beklenen nesne) ile `recall@k` ve `MRR` ölçülür.
- Reranker'lı/reranker'sız, kart-only/kart+chunk karşılaştırması bu set üzerinde yapılır → parametreler (k, eşik, chunk eşiği) buna göre ayarlanır. (Bkz. `13` yol haritası — değerlendirme adımı.)
