# 05 — Tablo Sözlüğü (Data Dictionary) ve Tablo Keşfi

## Amaç

Sistem sadece kod nesnelerini değil, **tabloların yapısını** da tanımalı: kolonlar, tipler, PK/FK, ilişkiler + LLM açıklaması. Böylece agent "müşteri telefonları hangi tabloda", "TEKLIF tablosu neyle ilişkili" gibi sorulara cevap verebilir.

Karar: **Şema + ilişki + LLM açıklama.** Veri profilleme (örnek değer, null oranı) **varsayılan kapalı** — canlı veriye dokunmaz (gizlilik + yük).

Kararlar (bu dosya): sadece **tanımlı FK** (örtük ilişki çıkarımı yok — kesinlik öncelikli); **view'lar da** kolon sözlüğü alır ve veri kaynağı sayılır; **zengin şema** (computed + check + default constraint yakalanır); PII etiketleme **yok** (varsayılan).

## Çekilen şema bilgisi (deterministik)

```sql
-- Kolonlar (UDT, computed, collation dahil)
SELECT c.name AS column_name,
       t.name AS data_type, t.is_user_defined AS is_udt, bt.name AS base_type,
       c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity,
       c.collation_name,
       dc.definition AS default_definition,
       cc.definition AS computed_definition       -- computed kolon ise dolu
FROM sys.columns c
JOIN sys.types t        ON t.user_type_id = c.user_type_id
LEFT JOIN sys.types bt  ON bt.user_type_id = t.system_type_id   -- UDT → temel tip
LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
LEFT JOIN sys.computed_columns cc    ON cc.object_id = c.object_id AND cc.column_id = c.column_id
WHERE c.object_id = OBJECT_ID('dbo.TEKLIF')
ORDER BY c.column_id;

-- Primary key
SELECT col.name
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
JOIN sys.columns col      ON col.object_id=ic.object_id AND col.column_id=ic.column_id
WHERE i.is_primary_key = 1 AND i.object_id = OBJECT_ID('dbo.TEKLIF');

-- Foreign key ilişkileri
SELECT fk.name AS fk_name,
       OBJECT_NAME(fk.parent_object_id)      AS from_table,
       OBJECT_NAME(fk.referenced_object_id)  AS to_table,
       cpa.name AS from_column, cre.name AS to_column
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns cpa ON cpa.object_id=fkc.parent_object_id     AND cpa.column_id=fkc.parent_column_id
JOIN sys.columns cre ON cre.object_id=fkc.referenced_object_id AND cre.column_id=fkc.referenced_column_id;
```
Ayrıca: index'ler (`sys.indexes`), **check constraint'ler** (`sys.check_constraints.definition` — ör. `Durum IN ('A','P','I')`, iş kuralı ipucu), satır sayısı **ve veri boyutu** tahmini (`sys.dm_db_partition_stats` — veri okumadan, sadece istatistik), kolon **extended property** açıklamaları (`03` — `class=1, minor_id>0`).

**View'lar:** Aynı `sys.columns` sorgusu view'larda da çalışır → view'lar da kolon-seviyesi sözlük kaydı alır ve **veri kaynağı** sayılır ("müşteri e-postası hangi view'da" cevaplanır). View'ın tanımı/bağımlılığı yine `03`/`04`'te; burada sadece açığa çıkardığı kolonlar.

## Tablo kaydı şeması — `tables/<schema>/<TABLE>.json`

```json
{
  "uid": "kasko-sql/KaskoDB/901578250",          // server/db/object_id (hibrit kimlik, 03)
  "alias": "kasko-sql/KaskoDB/dbo/TEKLIF",
  "server": "kasko-sql", "database": "KaskoDB", "schema": "dbo", "name": "TEKLIF",
  "object_kind": "table",                         // table | view
  "row_count_estimate": 1284553, "data_size_mb": 412,
  "columns": [
    {"name": "TeklifNo", "type": "INT", "nullable": false, "identity": true,
     "pk": true, "human_description": null, "description": null},
    {"name": "KullaniciID", "type": "INT", "nullable": false,
     "fk": {"to_table": "dbo.KULLANICI", "to_column": "ID"}, "description": null},
    {"name": "Sure", "type": "INT", "nullable": true, "collation": null,
     "human_description": "Teklif süresi (gün)",   // extended property → otoriter
     "description": null},
    {"name": "ToplamPrim", "type": "DECIMAL(18,2)", "computed": "([BrutPrim]+[Vergi])"}
  ],
  "primary_key": ["TeklifNo"],
  "foreign_keys": [
    {"name": "FK_TEKLIF_KULLANICI", "from": ["KullaniciID"],
     "to_table": "dbo.KULLANICI", "to": ["ID"]}
  ],
  "check_constraints": [{"name": "CK_TEKLIF_Durum", "definition": "[Durum] IN ('A','P','I')"}],
  "indexes": [{"name": "IX_TEKLIF_Tarih", "columns": ["BaslangicTarihi"], "unique": false}],
  "read_by_objects":    ["kasko-sql/KaskoDB/1234567"],   // uid (04: is_updated=false)
  "written_by_objects": ["kasko-sql/KaskoDB/2233445"],   // uid (04: is_updated=true)
  "table_description": null,    // LLM (aşağıda); human_description öncelikli
  "hash": "sha256:…"
}
```
`read_by`/`written_by` bağımlılık grafiğinden (`04`, `is_updated`) doldurulur — "TEKLIF'i kim okuyor / kim yazıyor" ayrımıyla. Kolon `human_description` (extended property) varsa `description`'tan önce gelir. Pipeline `state` Postgres'te (`01`/`03`).

## LLM açıklama katmanı

İki düzeyde, **offline / indexing-time**, ucuz model yeterli. **Önce insan, sonra LLM:** bir alanda `human_description` (extended property, `03`) varsa LLM o alana dokunmaz; LLM yalnızca boş kalanları doldurur.
- **Tablo açıklaması:** "Bu tablo ne tutar?" — kolon adları + FK + **check constraint'ler** (izinli değerler iş kuralını ele verir) + tabloyu kullanan SP özetlerinden çıkarım. (Canlı veri okumadan.)
- **Kolon açıklaması:** Belirsiz/kısaltmalı kolonlar için ("Sure" = teklif süresi gün?). Adı zaten açık kolonlar atlanır — token tasarrufu.

LLM'e verilen bağlam: kolon listesi + FK'lar + check constraint'ler + bu tabloyu kullanan ilk N SP'nin adı/özeti. Çıktı `table_description` ve boş `description` alanlarına yazılır.

### Enrichment kalite kapısı (karar — "zehirli embedding" önleme)
LLM özeti yanlış/uydurma olursa embedding'i bozar ve arama **sessizce** kötüleşir (`07` kart bunu kullanır).
Bu yüzden her LLM açıklaması, embed edilmeden önce **deterministik (LLM'siz, ucuz) bir tutarlılık
kontrolünden** geçer:
- **Çapraz-doğrulama:** Üretilen özette/açıklamada anılan tablo, kolon ve parametre adları gerçekten
  yapısal metadata'da (`04` `meta.json` / `05` tablo kaydı) var mı? Anılan ama var-olmayan ad =
  halüsinasyon sinyali.
- **Sonuç:**
  - Geçerse → `summary`/`description` yazılır, `summary_confidence: ok`.
  - Uyuşmazlık → **1 retry** (daha sıkı "yalnızca verilen alanları kullan" talimatıyla). Yine
    uyuşmazsa → alan **boş bırakılır** + `summary_confidence: low`; nesne **yapısal-only kartla**
    embed edilir (`07` fallback). Uydurma özet **embeddinge asla girmez.**
- **Kapsam:** Aynı kontrol kolon açıklamalarına da uygulanır (anılan kolon tabloda var mı?).
- **Ölçüm:** `summary_confidence: low` oranı `16`'ya metrik; eval'de (`15`) özet doğruluğu spot-check.

> Bu kontrol determinist olduğundan ucuzdur ve `09` JSON-şema doğrulamasının üstüne biner
> (şema *formatı* doğrular; bu kontrol *içeriğin gerçekliğini* doğrular).

### Neden profilleme kapalı?
- **Gizlilik:** Sigorta verisi; örnek değerler PII içerebilir.
- **Yük:** `SELECT TOP/DISTINCT` canlı tabloda maliyetli.
- **Yeterlilik:** Şema + ilişki + kullanım bağlamı, "hangi tablo/kolon" sorularına yetiyor.
- İleride istenirse `profiling.enabled: true` ile **maskelenmiş** örnekleme opsiyon olarak eklenebilir (sunucu/DB bazında açılır).

## Tablo keşfi (agent tarafı)

Tablolar **ve view'lar** embedding + keyword indeksine girer ("table card": ad + kolon adları + açıklama + ilişkili tablolar). Agent araçları:
- `search_tables(query)` → hybrid arama (ör. "müşteri iletişim bilgisi" → KULLANICI, ILETISIM; view'lar da döner).
- `describe_table(table)` → tam şema + ilişkiler + okuyan/yazan nesneler.
- `get_table_relations(table)` → **tanımlı FK** komşuları (join yolu önerisi). Örtük ilişki çıkarımı yok; ilişki yoksa "tanımlı FK bulunamadı" denir.

Bu sayede agent hem "şu işi yapan SP" hem "şu veriyi tutan tablo/view" sorularını aynı döngüde cevaplar; read/write ayrımıyla "TEKLIF'i **yazan** SP'ler hangileri" gibi sorulara da kesin cevap verir.

## Güncelleme

Tablo şeması da `04`'teki hash mekanizmasıyla incremental: kolon/FK değişimi hash'i değiştirir → o tablo yeniden parse + (gerekirse) yeniden açıklama + yeniden embed.
