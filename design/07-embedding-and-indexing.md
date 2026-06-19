# 07 — Embedding ve İndeksleme

## Amaç

Nesneleri ve tabloları aranabilir vektörlere çevirip PostgreSQL'de tek bir indekste tutmak. Karar: **BGE-M3** (varsayılan, swappable), **PostgreSQL + pgvector**, temsil olarak **object card + büyük nesnelerde chunk**.

## Embedding modeli — BGE-M3 (varsayılan)

- Güçlü **çokdilli** (Türkçe + İngilizce + kod karışımı), 1024 boyut, tek modelde **dense + sparse** üretebilir.
- Lokal çalışır (sentence-transformers / FlagEmbedding), Apache-uyumlu, GPU veya CPU.
- SP adları (`SP_TEKLIF_SURELERI`) + Türkçe yorumlar için saf kod-embedding'lerinden daha isabetli.
- **Prefix gerektirmez:** BGE-M3 query/passage için ayrı talimat istemez (e5/bge-v1.5'in aksine) — kart ve sorgu aynı şekilde kodlanır.

### Lexical strateji: dense + BGE-M3 **sparse** (+ trigram)
Karar: keyword tarafı `tsvector` yerine **BGE-M3'ün öğrenilmiş sparse vektörü** (pgvector `sparsevec`). Aynı modelden dense + sparse → tutarlı, çokdilli, öğrenilmiş lexical eşleşme. Tam/yazım-hatalı ad aramaları için ayrıca **`pg_trgm`** (trigram) indeks: `SP_TEKLF` veya `teklif sure` gibi kısmi/hatalı adlar yakalanır. Füzyon `08`'de (dense + sparse + trigram → RRF).

### Swappable tasarım
Embedding tek bir arayüzün arkasında (`embed(texts) -> vectors`), config'ten model seçilir:
```yaml
embedding:
  model: "bge-m3"          # alternatif: "qwen3-embedding-0.6b", "nomic-embed-code", "vertex:text-embedding-005"
  dim: 1024
  provider: "local"        # local | vertex | openai  (bkz. 09)
  normalize: true
```
**Kritik kural:** Model veya `dim` değişirse indeks tutarsız olur → otomatik **full re-embed + reindex** tetiklenir. Bu yüzden indekste `embedding_model` + `dim` damgası saklanır; uyumsuzluk görülürse pipeline reindex'e geçer (bkz. `11`).

### Re-embed sırasında servis: eski set ile devam (karar)
Full re-embed saatlerce sürebilir; bu pencerede **bozuk arama** olmamalı (`01` "her zaman cevap ver"
değişmezi reindex sırasında da geçerli). Karar: **eski tamamlanmış set ile servis et, yeni seti
arka planda doldur, bitince atomik swap.**
- Her embedding satırı zaten `embedding_model` + `dim` ile damgalı (yukarıdaki tablo). Re-embed,
  **yeni model damgasıyla yeni satırlar** üreterek ilerler; eski damgalı satırlar silinmez.
- **Sorgu, o kapsamda hâlâ "aktif" damgalı set üzerinden** çalışır (eski model). Sorgu embedding'i
  de aktif setin modeliyle üretilir → dense uzayı tutarlı, karışık-model cosine olmaz. `dim`
  değişse bile sorun yok (sorgu eski `dim`'i kullanır).
- Bir kapsam (server/db) için **tüm** nesneler yeni modelde tamamlanınca `active_embedding_model`
  damgası atomik olarak yeniye çevrilir (tek transaction); sonraki sorgular yeni seti kullanır;
  eski damgalı satırlar artık temizlenir.
- Böylece geçiş penceresinde kullanıcı **kesintisiz ve tutarlı** sonuç alır; yarı-bitmiş yeni set
  hiçbir zaman sorguya girmez. (Operasyonel akış: `11` reindex.)

## Temsil: ne embed edilir?

### Birincil — "object card" (her nesne için 1 vektör)
Tipik sorgu "şu işi yapan SP hangisi" anlamsaldır; ham SQL'in tamamı gürültü katar. Bu yüzden nesneyi özetleyen bir kart embed edilir:

```
[procedure] dbo.SP_TEKLIF_SURELERI
Özet: Kullanıcı bazında teklif sürelerini tarih aralığında hesaplar.
Kategori: teklif / sure-hesaplama
Parametreler: @KullaniciID INT, @BaslangicTarihi DATE, @BitisTarihi DATE
Döner: TeklifNo, Sure, Durum, HesaplamaTipi
Kullandığı tablolar: dbo.TEKLIF, dbo.SURE_TANIMLARI, dbo.KULLANICI_YETKI
Çağırdığı nesneler: dbo.SP_KULLANICI_YETKI_KONTROL
```
Bu kart `04`'teki yapısal metadata + `06`'daki kategori + özetten derlenir (özet = `human_description` varsa o, yoksa LLM özeti; `03`/`05`). Kısa, anlam-yoğun, token-verimli.

**Yapısal-only kart (fallback — özet yoksa/güvenilmezse):** Özet alanı **opsiyoneldir.** Enrichment
henüz çalışmamışsa (M1–M3, LLM'siz aşama), başarısızsa (`parse_error`/LLM down), veya kalite
kapısından (`05`/`06` tutarlılık kontrolü) **düşük güvenle** geçmişse → kart, "Özet" satırı
**olmadan** yalnızca yapısal alanlardan (ad + tip + parametre + tablo + kategori) derlenir ve
yine de embed edilir. Böylece nesne aramada **hiç görünmez kalmaz**; özet gelince yeniden embed
edilir. (Düşük güvenli/uydurma özet karta **konmaz** — `05`/`06` kararı; zehirli embedding önlenir.)

### İkincil — gövde chunk'ları (sadece büyük/karmaşık nesneler)
Bazı SP'ler binlerce satır; kart bazen yetmez ("şu spesifik hesaplama nerede geçiyor"). Eşik üstü (ör. > 300 satır) nesneler için ham SQL **mantıksal bloklara** bölünüp ek vektörler üretilir:
- Bölme: ifade sınırlarında (sqlglot statement split), aşırı uzunsa pencereleme + örtüşme.
- Bu chunk'lar kartla aynı `object_id`'ye bağlı; sonuçlar nesne düzeyinde toplanır (aynı SP iki kez listelenmez).

### Tablolar/view'lar — "table card"
`05`'teki kayıttan: ad + kolon adları + (insan/LLM) açıklama + FK komşuları → 1 vektör. View'lar da (`05` kararı) bu şekilde indekslenir. Tablo/veri keşfi bunu kullanır.

### Kategori/klasör özetleri (karar: evet)
Her kod ve veri kategorisinin özeti (`06` README/catalog) de embed edilir (`kind='category'`). "Beni teklif alanına götür", "raporlama ile ne var" gibi üst-seviye sorular bununla cevaplanır; recall zenginleşir.

### Neden bu hibrit temsil?
- Sadece kart: büyük nesnelerin iç detayını kaçırır.
- Sadece chunk: indeks şişer, "genel ne yapar" sorusunda parçalı/gürültülü sonuç.
- Kart (her zaman) + chunk (gerekince) + kategori (üst-seviye): hem genel hem detay hem gezinme; maliyet kontrollü.

## PostgreSQL şeması (pgvector)

Tek DB; vektör + sparse + trigram + metadata + graph. Şema **SQL-dosya migration + runner** ile versiyonlanır (`01`/`13`).

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- dense + sparsevec
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- bulanık/kısmi isim

CREATE TABLE objects (
  uid           TEXT PRIMARY KEY,          -- server/db/object_id (KALICI, 03/04)
  alias         TEXT,                      -- server/db/schema/name (okunur, rename'de değişir)
  server        TEXT, database TEXT, schema TEXT, name TEXT,
  type          TEXT,                      -- procedure|view|function|trigger|table
  object_kind   TEXT,                      -- code | table | view (06: kod/veri taksonomisi)
  category      TEXT, subcategory TEXT,     -- birincil (kod için)
  secondary_categories TEXT[],             -- ikincil etiketler (06)
  data_category TEXT,                       -- tablo/view veri-alanı (06)
  pinned        BOOLEAN DEFAULT FALSE,      -- elle düzeltme korunur (06)
  summary       TEXT, human_description TEXT,
  meta          JSONB,                     -- tam meta.json
  hash          TEXT, schema_version INT,
  state         TEXT,                      -- extracted→…→indexed (01/03)
  updated_at    TIMESTAMPTZ
);

CREATE TABLE embeddings (
  chunk_id        BIGSERIAL PRIMARY KEY,
  uid             TEXT REFERENCES objects(uid) ON DELETE CASCADE,
  kind            TEXT,                    -- 'card' | 'body' | 'table' | 'category'
  content         TEXT,
  embedding       vector(1024),            -- dense (tam float32, karar)
  sparse          sparsevec,               -- BGE-M3 öğrenilmiş sparse (lexical)
  embedding_model TEXT, dim INT
);

CREATE TABLE edges (                       -- bağımlılık grafiği (04)
  src_uid TEXT, dst_uid TEXT,
  kind    TEXT,                            -- calls | reads | writes
  via_synonym BOOLEAN DEFAULT FALSE
);

-- indeksler
CREATE INDEX ON embeddings USING hnsw (embedding vector_cosine_ops);   -- dense ANN
CREATE INDEX ON embeddings USING hnsw (sparse sparsevec_ip_ops);       -- sparse ANN
CREATE INDEX ON objects USING gin (name gin_trgm_ops);                 -- fuzzy/kısmi ad
CREATE INDEX ON objects USING gin (secondary_categories);
CREATE INDEX ON objects (server, database, object_kind, category);
CREATE INDEX ON embeddings (uid, embedding_model);                     -- aktif-set / re-embed swap (1.2)
CREATE INDEX ON edges (src_uid);                                       -- get_dependencies (04)
CREATE INDEX ON edges (dst_uid);                                       -- get_dependents / etki analizi (04)
```
> İndeks disiplini: her sık-filtrelenen kolon (scope, kategori) ve her graph yön-sorgusu (src/dst)
> için indeks baştan tanımlanır; eksik indeks bu ölçekte bile seq-scan'e düşürür. Migration
> dosyalarında (`13`) indeksler şema ile birlikte versiyonlanır.

- **Vektör:** HNSW (hızlı ANN), dense + sparse ayrı. `m`/`ef_construction` config'ten.
- **Lexical:** BGE-M3 sparse (öğrenilmiş) + `pg_trgm` (tam/yazım-hatalı ad).
- **Depolama:** tam `float32` vektör (karar — bu ölçekte hassasiyet > tasarruf).
- **Filtre:** `server/database/object_kind/category/secondary_categories` ile kapsam daraltma (multi-tenant).
- **Re-embed damgası:** sorgu yalnızca o kapsamın **aktif** `embedding_model`/`dim` damgalı satırlarını okur (re-embed sırasında eski set; yukarıdaki swap kararı). Bunun için `embeddings(uid, embedding_model)` üzerinde yardımcı indeks.

### Türkçe tokenizasyon ve collation (karar)
Trigram ad-araması (`08` `name` niyetinde **birincil**) Türkçe'de doğru çalışmalı; bu bir
parantez-notu değil, açık bir karardır:
- **Sorun:** noktalı/noktasız I (İ/ı ↔ I/i), `LOWER()`'ın varsayılan collation'a bağlı yanlış
  katlaması, `unaccent`'in Türkçe karakterleri (ş/ç/ğ/ö/ü) agresif ezmesi → `SP_TEKLİF` ≠ `SP_TEKLIF`
  yanlış pozitif/negatif.
- **Karar:** Ad/lexical normalizasyonu **uygulama katmanında deterministik** yapılır (Python,
  açık Türkçe katlama tablosu) — DB collation'ına bağımlı kalınmaz; trigram'a hep aynı
  normalize biçim verilir. `unaccent` Türkçe-özel karakterleri **korur** (yalnızca aksan değil
  Türkçe-bilinçli fold). Hem ham ad hem normalize ad saklanır (`objects.name` + türetilmiş
  arama alanı) → kullanıcıya gerçek ad, aramaya normalize ad.
- **Doğrulama:** `15` unit testlerine Türkçe ad korpusu (İ/ı, ş/ç/ğ varyantları) eklenir.

### Neden pgvector / tek DB?
- Vektör + keyword + metadata + graph tek yerde → "basit/dengeli" önceliği.
- Railway PostgreSQL'e eklenti; ekstra servis yok.
- Alternatifler (Qdrant, LanceDB) ölçek/özellik getirir ama bu ölçekte (~birkaç bin nesne/DB) gereksiz operasyon yükü.

## Incremental indeksleme

- Sadece `state != indexed` veya hash'i değişen nesneler embed edilir (kimlik `uid`).
- Nesne silindiyse `ON DELETE CASCADE` ile dense+sparse embeddings birlikte düşer.
- Model/dim damgası uyuşmazsa → ilgili kapsam (server/db) full re-embed (dense+sparse).
- Kategori özetleri (`kind='category'`) yalnızca ilgili kategori değiştiğinde yeniden embed edilir (`06` güncelleme davranışı).
- Embedding batch'lenir (GPU verimliliği); BGE-M3 tek geçişte dense + sparse üretir.
- Upsert transactional + nesne-başı (`01`: eventually consistent, yarı-yazım görünmez).
