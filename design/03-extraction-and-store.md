# 03 — Extraction ve Lokal Store

## Amaç

Keşfedilen nesnelerin **tanımlarını** ve **tablo şemalarını** lokale, deterministik biçimde indirmek; sadece **değişenleri** yeniden işlemek. Git kullanılmaz — sistem kendi store'unu yönetir.

## Ne çekilir?

Karar: **tüm programlanabilir nesneler + tablo şeması.**

| Tür | Kaynak | İçerik |
|---|---|---|
| Procedure | `sys.sql_modules.definition` | Ham SQL gövdesi |
| View | `sys.sql_modules.definition` | Ham SQL |
| Function (scalar/inline/table) | `sys.sql_modules.definition` | Ham SQL |
| Trigger | `sys.sql_modules.definition` | Ham SQL |
| Table | `sys`/`INFORMATION_SCHEMA` | Şema (kolon, tip, PK/FK, index) — bkz. `05` |

Tanım sorgusu (tek geçişte tüm modüller):
```sql
SELECT o.object_id, s.name AS schema_name, o.name AS object_name,
       o.type_desc, o.modify_date, m.definition
FROM sys.sql_modules m
JOIN sys.objects o  ON o.object_id = m.object_id
JOIN sys.schemas s  ON s.schema_id = o.schema_id;
```
Ayrıca her modülün **semantik bayrakları** meta'ya alınır: `uses_ansi_nulls`, `uses_quoted_identifier`, `is_recompiled`, `execute_as_principal` (çalıştırma bağlamı). Bunlar davranışı etkiler, aramada/incelemede değerli.

> **Encrypted nesneler:** `WITH ENCRYPTION` olanlar `definition = NULL` döner → "encrypted" flag'i, gövde yok, sadece metadata.
> **CLR/assembly nesneleri** (`type IN ('PC','FS','FT','AF')`): T-SQL tanımı yoktur; "clr" flag'i + assembly adı tutulur, gövde aranabilir değildir.
> **DDL trigger'lar:** DML trigger'lara ek olarak DB/sunucu kapsamı DDL trigger'lar da (`sys.triggers` parent_class>0 + `sys.server_sql_modules`) kapsanır; `trigger_scope: dml|ddl_db|ddl_server` ile ayrılır.

### Extended properties (mevcut insan dokümantasyonu) hasadı

Kaynak DB zaten `MS_Description` gibi extended property'lerle dokümante edilmiş olabilir. Bu **otoriter insan bilgisi** — hasat edilir ve açıklamada **önceliklenir** (LLM yalnızca boş kalanları doldurur, `05`/enrichment):
```sql
SELECT major_id, minor_id, name AS prop_name, CAST(value AS NVARCHAR(MAX)) AS prop_value
FROM sys.extended_properties
WHERE class = 1;          -- 1=object/column (minor_id=0 → nesne, >0 → kolon)
```
Nesne için → `meta.human_description`; kolon için → tablo sözlüğünde ilgili kolonun `description`'ı (`05`). Hem bedava/doğru dokümantasyon hem LLM maliyeti düşer.

## Lokal store düzeni

Çok-sunucu/çok-DB izolasyonu için ağaç:

```
data/
├── <server_id>/
│   ├── _server.json                  # keşfedilen + pending DB'ler, degraded durum (02)
│   └── <database>/
│       ├── _manifest.json            # keşif + hash kaydı (02)
│       ├── _taxonomy.json            # DB-başına kategori ağacı (06)
│       ├── _changelog.jsonl          # değişim olayları (zaman, id, eski/yeni hash, run_id)
│       ├── procedures/
│       │   └── dbo/
│       │       ├── SP_TEKLIF_SURELERI.sql        # ham SQL (UTF-8)
│       │       ├── SP_TEKLIF_SURELERI.meta.json  # yapısal metadata (04)
│       │       └── SP_TEKLIF_SURELERI.prev.sql   # bir önceki sürüm (opsiyonel, derinlik=1)
│       ├── views/dbo/...
│       ├── functions/dbo/...
│       ├── triggers/dbo/...
│       └── tables/dbo/
│           └── TEKLIF.json           # tablo sözlüğü kaydı (05)
```

- **Dosya adları sanitize edilir:** geçersiz karakterler (`[ ] / \ : *` vb., Türkçe karakterler korunur ama dosya-güvenli forma indirgenir), çakışmada kısa son-ek (`__2`). **Gerçek ad** her zaman `meta.json` + `_manifest.json`'da tam haliyle durur; dosya adı sadece okunabilir bir kolaylık.
- **Atomik yazım:** her dosya temp'e yazılıp `rename` ile yerine konur → çökme anında bozuk/yarım dosya olmaz.
- **Encoding:** kaynak NVARCHAR (UTF-16) → diskte UTF-8 (BOM'suz).

Klasörleme katmanı (06) bunun **üstünde** anlamsal bir görünüm üretir; ham store her zaman tür+şema ekseninde kalır (stabil, deterministik). Anlamsal klasörler ayrı bir `catalog/` ağacında ya da metadata etiketleriyle ifade edilir — ham dosyalar taşınmaz (bkz. `06`).

### Nesne kimliği (hibrit: kalıcı id + okunur ad)

İki kimlik birlikte tutulur:
- **Kalıcı kimlik (otorite):** `server_id / database / object_id`. MSSQL `object_id` bir DB içinde sabittir; rename'de **değişmez**.
- **Okunur alias (adresleme):** `server / database / schema / name`. API, agent ve dosya yolu bunu kullanır (insan dostu).

Eşleştirme önce `object_id` ile yapılır; bu sayede **rename = aynı nesnenin ad değişimi** (dosya taşınır, alias güncellenir, embedding/geçmiş **korunur**) — delete+add değil. Yeni `object_id` → gerçek ekleme; kaybolan `object_id` → gerçek silme. Alias çakışması/şema taşınması da yine `object_id` üzerinden çözülür.

### Neden ham SQL + ayrı `.meta.json`?
- Ham SQL: agent "tam içeriği oku" dediğinde kaynak.
- `.meta.json`: parse çıktısı + LLM özeti + kategori + hash. Embedding ve retrieval bunu kullanır, ham SQL'i sadece gerektiğinde okur (token tasarrufu).

## Değişim tespiti — modify_date + content hash

Karar: **modify_date ile aday, SHA256 ile doğrulama.**

Akış (her schedule çalışmasında), **kimlik = `object_id`** üzerinden:
1. Keşiften gelen `(object_id, name, modify_date)` listesini al.
2. Önceki `_manifest.json` ile karşılaştır:
   - Yeni `object_id` → **eklendi**.
   - Kaybolan `object_id` → **silindi** (yalnızca keşif tam başarılıysa — aşağıdaki soft-delete güvenliği).
   - Aynı `object_id`, farklı `name`/`schema` → **rename/taşıma** (dosya taşı, alias güncelle, yeniden işleme yok; sadece içerik de değiştiyse normal akış).
   - `modify_date` değişmiş → **aday**.
3. Adaylar için tanımı çek, `SHA256(normalize(definition))` hesapla.
   - Hash farklıysa → **gerçekten değişti** → bir önceki `.sql`'i `.prev.sql`'e taşı, `_changelog.jsonl`'a olay yaz, yeniden işle.
   - Hash aynıysa (ör. `ALTER` dokunmuş ama içerik aynı) → atla.
4. `_manifest.json` güncellenir (yeni name + modify_date + hash); değişim `_changelog.jsonl`'a (zaman, object_id, eski→yeni hash, run_id) eklenir.

```
normalize(sql) = satır sonlarını LF'e çevir + trailing whitespace kırp
                 (yorumları KORU — anlamsal arama için değerli;
                  string literal içini DEĞİŞTİRME — semantik bozulmasın)
```

**Soft-delete güvenliği:** Bir `object_id` envanterden eksikse "silindi" demeden önce o DB'nin keşfi **tam başarılı** olmalı. DB degraded/erişilemez/kısmi okunduysa, eksik nesneler **silinmez** — son iyi snapshot korunur. Bu, geçici ağ/izin sorununun yanlışlıkla toplu silmeye yol açmasını engeller.

### Neden iki aşama?
- **Sadece modify_date:** Bazı ortamlarda `ALTER` içerik değiştirmeden tarihi günceller → gereksiz reindex.
- **Sadece hash:** Her çalışmada tüm nesnelerin tanımını çekmek gerekir (2000+ ağ trafiği).
- **İkisi:** modify_date ucuz ön-filtre (sadece object_id+tarih çeker), hash kesin doğrulama. En verimli.

## Değişim geçmişi, silme / arşivleme politikası

Karar: **changelog + bir önceki sürüm** (dengeli, hafif audit).
- **`_changelog.jsonl`:** her değişim/ekleme/silme/rename olayı (zaman, object_id, alias, eski→yeni hash, run_id). Sınırsız büyümez (olay başına tek satır, çok küçük).
- **Bir önceki sürüm:** değişimde eski tanım `.prev.sql` olarak tutulur (config `history_depth`, varsayılan 1; 0 = kapalı). "Tam olarak ne değişti" diff'i bununla yapılır.
- **Silme:** soft-delete güvenliği geçtiyse nesne store'dan kaldırılır, indeksten düşürülür, `_changelog`'a `removed` olayı yazılır; istenirse `.prev.sql` bir süre tutulur.
- Tam append-only geçmiş bilinçle seçilmedi (disk şişer, git'siz kalma gerekçesiyle çelişir).

## Idempotensi ve güvenli yeniden çalışma

- Pipeline her adımı yeniden çalıştırılabilir (crash sonrası kaldığı yerden).
- **Per-nesne pipeline state'i Postgres'te** (`objects.state`: `extracted → parsed → enriched → embedded → indexed`) — `01`'deki "disk=içerik, Postgres=indeks/durum" ayrımına uygun. Worker yarıda kesilirse, `indexed` olmayan nesneler kuyruktan/işten kaldığı yerde devam eder.
- Disk yazımı atomik olduğundan, içerik dosyaları her zaman tutarlı (tam ya da hiç).

## meta.json şema sürümü ve göç (migration)

`meta.json` içinde `schema_version` tutulur. Sistem güncellenip şema değişirse (yeni alan, yeniden adlandırma):
- Açılışta disk `schema_version` < kod sürümü ise → **lazy/batch migrator** eski meta'yı yeni şemaya dönüştürür (genelde alan ekleme/yeniden adlandırma; içerik yeniden çekmeden).
- Dönüşüm gerektiren alan parse'tan türetilebiliyorsa yeniden türetilir; türetilemiyorsa o nesne yeniden işleme kuyruğuna alınır.
- Postgres tarafı SQL-dosya migration runner ile (`01`/`13`); disk ve DB şema sürümleri birlikte yükseltilir. Böylece şema evrimi **full-rebuild gerektirmez**.

## Performans notları

- Extraction tek `sys.sql_modules` sorgusuyla toplu çekilir (N+1 değil).
- 2000 SP için tipik tam çekim saniyeler sürer; incremental çalışmalarda yalnızca değişen birkaç nesne işlenir.
- Hash hesaplama lokal/CPU — ihmal edilebilir.

## İntra-run kaynak değişimi (not — `REVIEW-gap-analysis` 3.6)

Extraction dakikalar sürebilir; envanter snapshot'ı (`02` keşif) ile tanım çekimi arasında bir SP
`ALTER`'lanırsa **"yırtık okuma"** olabilir (snapshot'taki `modify_date` ile çekilen gövde anlık
tutarsız). Bu **bilinçli kabul edilen** bir durumdur: sistem tutarlı-anlık (point-in-time) okuma
denemez. Bir sonraki sync'te değişen `modify_date` + hash farkı nesneyi yeniden yakalar
(**eventual catch-up**). Katalog kullanımı için bu gecikme önemsizdir; kaynak DB salt-okunur ve
hiçbir karar bu ara-tutarsızlığa dayanmaz.
