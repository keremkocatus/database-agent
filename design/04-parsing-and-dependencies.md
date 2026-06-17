# 04 — Parsing ve Bağımlılık Grafiği

## Amaç

Her nesnenin ham SQL'inden **yapısal metadata** çıkarmak (parametreler, dönen tip, kullanılan tablolar, çağrılan nesneler) ve bunlardan bir **bağımlılık grafiği** kurmak. Bu katman tamamen deterministik — LLM yok.

## İki kaynak: server-side + parser (ikisi birlikte)

MSSQL bağımlılığı **iki yoldan** çıkar; birbirini doğrularlar:

### 1) Server-side bağımlılıklar (birincil) — bulk öncelikli
Karar: **bulk `sys.sql_expression_dependencies` birincil, `dm_sql_referenced_entities` yedek.** DB-geneli tek sorgu N+1'i önler; belirsiz/dinamik durumlarda nesne-bazında dm ile doğrulanır.
```sql
-- Birincil: DB-geneli tek geçiş (hızlı, 2000+ nesnede tek sorgu)
SELECT referencing_id,
       referenced_database_name, referenced_schema_name, referenced_entity_name,
       referenced_minor_name,          -- kolon (referans kolon lineage'ı, ucuz)
       is_updated                      -- TRUE → writes, FALSE → reads
FROM sys.sql_expression_dependencies;

-- Yedek: yalnızca belirsiz/caller-dependent/dinamik şüphesi olan nesneler için
SELECT referenced_schema_name, referenced_entity_name, referenced_minor_name, is_updated
FROM sys.dm_sql_referenced_entities('dbo.SP_TEKLIF_SURELERI', 'OBJECT');
```
- **read/write ayrımı:** `is_updated` → tablo yazılıyor mu (INSERT/UPDATE/DELETE) yoksa okunuyor mu. `reads`/`writes` kenarı buradan doğru dolar; "bu tabloyu kim yazıyor" cevaplanır.
- **referans kolonlar:** `referenced_minor_name` ucuza kolon-seviyesi referans verir ("SP, TEKLIF.Sure'yi kullanıyor"). Tam kolon-akış lineage'ı **kapsam dışı** (bilinçli — T-SQL'de kırılgan/pahalı).
- **synonym çözümleme:** hedef bir synonym ise `02`'deki çözümlemeyle gerçek hedefe bağlanır (`via_synonym: true`).

### 2) Parser (tamamlayıcı, yapısal detay)
Server-side bağımlılık **parametreleri, dönen kolonları, geçici tabloları** vermez. Bunun için ham SQL parse edilir.

Karar: **`sqlglot`** (tsql dialect), **regex-lite fallback** ile.
- AST üretir, scope modülü var, T-SQL dialect destekli, saf-Python, hızlı, bağımlılığı hafif.
- **Alternatif — `sqlfluff`:** Esasen linter; AST erişimi daha zahmetli. Stil denetimi gerekmiyor.
- **Seçim:** sqlglot birincil; server-side bağımlılıkla çapraz-doğrulama.

Parser çıktısı:
- Parametreler: ad, tip (UDT ise temel tipe çözülür + `udt` bayrağı), default, OUTPUT mu.
- Dönen yapı: `RETURNS TABLE(...)` / son `SELECT` kolonları (mümkün olduğunca).
- Kullanılan tablolar/view'lar, çağrılan SP/function'lar (server-side ile birleşir).
- Geçici tablolar (`#temp`), tablo değişkenleri, CTE'ler.

**Fallback (sqlglot başarısızsa):** Karmaşık T-SQL (MERGE, PIVOT, ile hint'ler, alışılmadık sözdizimi) parse edilemezse:
1. Bağımlılıklar zaten **server-side**'dan gelir (etkilenmez).
2. Parametre/yapı için **regex-lite** ile kısmi çıkarım yapılır (parametre bloğu, `RETURNS`).
3. Nesne `partial_parse: true` işaretlenir — hiç yapı çıkaramamaktan iyidir, aramada zayıf kalmaz.

## Yapısal metadata şeması — `*.meta.json`

```json
{
  // hibrit kimlik (03): kalıcı id + okunur alias
  "uid": "kasko-sql/KaskoDB/1234567",          // server/db/object_id — KALICI
  "alias": "kasko-sql/KaskoDB/dbo/SP_TEKLIF_SURELERI", // okunur, rename'de değişir
  "server": "kasko-sql", "database": "KaskoDB",
  "schema": "dbo", "name": "SP_TEKLIF_SURELERI",
  "type": "procedure", "object_id": 1234567,
  "modify_date": "2026-05-02T09:15:00Z",
  "hash": "sha256:…",
  "flags": {"encrypted": false, "clr": false, "has_dynamic_sql": false,
            "partial_parse": false, "uses_ansi_nulls": true},

  "parameters": [
    {"name": "@KullaniciID", "type": "INT", "udt": false, "output": false, "default": null}
  ],
  "returns": {"kind": "table", "columns": ["TeklifNo", "Sure", "Durum", "HesaplamaTipi"]},

  "reads_tables":  [{"name": "dbo.TEKLIF", "columns": ["TeklifNo","Sure"]},
                    {"name": "dbo.SURE_TANIMLARI"}],
  "writes_tables": [{"name": "dbo.TEKLIF_LOG"}],
  "calls_objects": ["dbo.SP_KULLANICI_YETKI_KONTROL"],
  "temp_tables": ["#GeciciSure"],
  "loc": 240,

  "human_description": "MS_Description'dan (03) — varsa otoriter",
  "summary": null,          // LLM tarafından doldurulur (06); human_description öncelikli
  "category": null,         // LLM tarafından doldurulur (06)
  "state": "parsed"         // pipeline state Postgres'te de tutulur (01/03)
}
```

`reads_tables`/`writes_tables` ayrımı server-side `is_updated`'ten gelir. `human_description` (varsa) `summary`'den önce gelir. `summary`/`category` enrichment'ta (`06`) doldurulur.

## Bağımlılık grafiği

- **Düğümler:** nesneler (SP/View/Function/Trigger) + tablolar. Tümü kalıcı `uid` (`server/db/object_id`) ile.
- **Kenarlar:** `calls`, `reads`, `writes` (+ trigger→tablo / tablo→trigger). `edges(src_uid, dst_uid, kind, via_synonym)`.
- **Kapsam kuralı (karar):** Yalnızca **kapsam içi** hedeflere kenar kurulur. Hedef başka bir keşfedilmiş+onaylı DB ise cross-DB kenar gerçek düğüme bağlanır. Kapsam dışı DB / linked-server (4-parçalı ad) hedefleri **düşürülür** (external düğüm yaratılmaz) — graf temiz kalır. (İstenirse ileride "external" moduna geçilebilir; şema bunu engellemiyor.)
- **Depolama:** Postgres `edges` tablosu (tek DB ilkesi; ayrı graph DB yok). Mermaid/GraphViz export opsiyonel.
- **Kullanım:**
  - `get_dependencies(object)`: çağırdıkları + okuduğu/yazdığı tablolar.
  - `get_dependents(object)`: "bu tabloyu/SP'yi kim kullanıyor/yazıyor" (etki analizi; read/write ayrımıyla).
  - Multi-hop: `SP_A → SP_B → tablo_C` — tipik 2-3 hop, recursive CTE.
  - **Cycle guard:** Bağımlılıklar döngü içerebilir (`SP_A→SP_B→SP_A`). Recursive CTE'de ziyaret-edilen `uid` yolu izlenir (PostgreSQL `CYCLE` cümlesi veya path dizisi) + **derinlik limiti** (ör. 6) → sonsuz döngü/patlama engellenir.

### Neden ayrı graph DB değil?
510 tablo + ~2200 nesne ölçeğinde kenar sayısı on binler mertebesinde. Postgres recursive CTE bunu rahat kaldırır. Neo4j gibi bir servis "basitlik" önceliğine aykırı olurdu.

## Dinamik SQL ve sınırlar

- `EXEC(@sql)` / `sp_executesql` ile kurulan dinamik sorguların hedefleri statik parse edilemez.
- Bu nesneler `has_dynamic_sql: true` ile işaretlenir; bağımlılıkları "eksik olabilir" notuyla saklanır.
- Enrichment aşamasında LLM, dinamik SQL içinden olası tablo adlarını **tahmini** olarak çıkarabilir (ayrı alanda, "kesin değil" etiketiyle).

## Doğrulama

- Bağımlılıkta **server-side otorite**; parser çıktısı çelişirse server-side kazanır, fark log'lanır (parser iyileştirme sinyali).
- Tam parse başarısızsa `partial_parse: true` (regex-lite ile kısmi yapı). Bağımlılıklar yine de server-side'dan tam gelir; nesne aramada zayıf kalmaz.
- Tümüyle çözümlenemeyen nadir nesne `state: "parse_error"` ile işaretlenir, pipeline durmaz; ham SQL yine saklanır ve keyword'le aranabilir.
