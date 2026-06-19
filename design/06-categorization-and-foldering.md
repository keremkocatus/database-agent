# 06 — Kategorizasyon ve Klasörleme

## Amaç

Nesneleri hem **yapısal** (Server/DB/Schema/Tür) hem **anlamsal** (finans, poliçe, müşteri, raporlama…) olarak organize etmek ve her klasör için metadata üretmek. Karar: **Hibrit (yapı + anlam)**, **DB-başına taksonomi**, klasör metadata'sı **yapısal JSON + README MD**.

Bu dosyanın kararları: **iki ayrı taksonomi** — kod nesneleri (SP/View/Function/Trigger) için *kod taksonomisi*, tablo/view veri alanları için ayrı *veri taksonomisi*; her nesne **birincil + ikincil etiket** alır; taksonomi **seed + embedding-kümeleme** ile üretilir; **pinned override** (elle düzeltme) desteklenir.

## İki katmanlı klasörleme

### Katman 1 — Yapısal (deterministik, `03`'teki store)
Ham dosyalar her zaman `data/<server>/<db>/<tür>/<schema>/` altında. Stabil, tekrarlanabilir, taşınmaz. Bu "fiziksel" gerçek.

### Katman 2 — Anlamsal (LLM, sanal görünüm)
Anlamsal kategoriler ham dosyaları **taşımaz**; bunun yerine `catalog/` altında bir görünüm + metadata etiketi üretir:

İki ayrı anlamsal taksonomi, `catalog/` altında ayrı ağaçlarda. Ham dosyalar **taşınmaz**; üyelik metadata ile kurulur:

```
data/<server>/<db>/
├── procedures/… views/… tables/…    # fiziksel (taşınmaz, 03)
└── catalog/
    ├── code/                         # KOD taksonomisi (SP/View/Function/Trigger)
    │   ├── _taxonomy.json
    │   ├── teklif/   { README.md, catalog.json }
    │   ├── police/   { README.md, catalog.json }
    │   └── diger/    …
    └── data/                         # VERİ taksonomisi (tablo/view veri alanları)
        ├── _taxonomy.json
        ├── teklif-verisi/  { README.md, catalog.json }
        ├── musteri-verisi/ { README.md, catalog.json }
        └── diger/          …
```

- **Kod nesnesi** üyeliği: `*.meta.json` içinde `category` (birincil) + `secondary_categories` (ikincil etiketler) + `subcategory`.
- **Tablo/view** üyeliği: tablo kaydında (`05`) `data_category` (birincil) + `data_secondary`.
- `catalog/*/<kategori>/catalog.json` bu alanlardan derlenir; üyelere **`uid`** ile referans verir (`03`/`04`).

### Neden taşımıyoruz / neden iki taksonomi?
- Bir SP birden çok kategoriye uyabilir → fiziksel taşımada çakışır; metadata etiketi (birincil + ikincil) bunu temiz çözer.
- Fiziksel store stabil kalır → değişim tespiti (hash) bozulmaz.
- Kod ile veri farklı eksenler: "teklif **işini yapan kod**" ile "teklif **verisini tutan tablolar**" ayrı sorular; ayrı taksonomi her birini net tutar. (Kod↔veri bağı zaten bağımlılık grafiğinde, `04`.)

## DB-başına taksonomi (kod + veri ayrı)

Karar: her veritabanının **kendi kategori ağaçları** — `catalog/code/_taxonomy.json` ve `catalog/data/_taxonomy.json`. Farklı DB'ler farklı iş alanları → ortak global taksonomi yapay olurdu. İkisi aynı şemayı paylaşır (aşağıdaki örnek koda ait; veri taksonomisi de birebir aynı yapı, sadece kategoriler veri alanları).

```json
{
  "database": "KaskoDB",
  "version": 3,
  "generated_at": "2026-06-17T03:10:00Z",
  "categories": [
    {"key": "teklif", "label": "Teklif & Fiyatlama",
     "description": "Teklif oluşturma, süre/prim hesaplama",
     "subcategories": ["sure-hesaplama", "prim-hesaplama"]},
    {"key": "police", "label": "Poliçe Yönetimi", "subcategories": ["uretim","iptal","zeyil"]},
    {"key": "musteri", "label": "Müşteri & Kullanıcı", "subcategories": ["yetki","iletisim"]},
    {"key": "raporlama", "label": "Raporlama", "subcategories": []},
    {"key": "diger", "label": "Diğer / Sınıflandırılmamış", "subcategories": []}
  ]
}
```

### Taksonomi nasıl oluşur? (Seed + embedding-kümeleme destekli)
Tamamen emergent taksonomi tutarsızlaşır; tamamen sabit yeni alanı kaçırır; LLM'in örneklemden uydurması temsil sorunlu olur. Karar: **bottom-up, veriye dayalı**:
1. **Tohum (seed):** Config'te opsiyonel başlangıç kategorileri (ör. sigorta için teklif/poliçe/hasar/müşteri/raporlama). Boş da bırakılabilir.
2. **Embedding-kümeleme:** Nesne özeti embedding'leri (`07`) kümelenecek (ör. HDBSCAN/k-means); her küme **LLM ile etiketlenir** (kümenin ortak işi → kategori adı + açıklama). Veriye dayalı, açıklanabilir kategoriler.
3. **Seed ile birleştir:** Kümelerden çıkan adaylar tohum kategorilerle harmanlanır; benzer olanlar tohuma katlanır, yeni güçlü kümeler önerilir.
4. **Stabilizasyon:** Kategori ekleme/değiştirme **versiyonlanır** (`version` artar). Mevcut üyelikler korunur; sadece yeni/değişen nesneler yeniden eşlenir → taksonomi **dağılmaz**, kontrollü büyür.
5. Eşleşmeyen / düşük güvenli nesneler `diger`'e düşer + öneri listesine girer (insan onayına açık).

Kod ve veri taksonomileri bu süreçten **bağımsız** geçer (kod özetleri ayrı kümelenir, tablo "card"ları ayrı).

### Taksonominin kendi göçü — kategori rename / merge / split (karar)
Nesne→kategori yeniden-eşleme yetmez; **kategorinin kendisi** değişebilir (ad değişir, iki kategori
birleşir, biri ikiye bölünür). Bu durumda `pinned_category`, `secondary_categories`, `catalog/`
klasörleri ve `catalog.json` referansları kırılmamalı. Taksonomi versiyon diff'i açık **göç olayları**
üretir:
- **`rename` (key A → B):** Tüm üyelik referansları (`category`, `secondary_categories`, `pinned_category`)
  otomatik A→B remap edilir; `catalog/code/A/` → `catalog/code/B/`'ye taşınır (klasör yeniden adlandırılır).
- **`merge` (A + B → C):** Her iki kategorinin üyeleri C'ye taşınır; çakışan birincil/ikincil
  tekilleştirilir; A ve B klasörleri **tombstone** ile işaretlenip C'ye yönlendirilir (eski link kırılmaz).
- **`split` (A → A1, A2):** A üyeleri **yeniden sınıflandırılır** (pinned olmayanlar); pinned üyeler
  operatör onayına düşer (otomatik bölünmez — insan kürasyonu korunur).
- **Pinned koruması:** `rename`/`merge`'de pinned referans remap edilir ama **kategori içeriği**
  korunur; `split`'te pinned asla otomatik taşınmaz.
- **Atomiklik + audit:** Göç tek taksonomi-versiyon yükseltmesinde uygulanır; her olay `_changelog`
  ve run-store'a yazılır (geri izlenebilir). Etkilenen kategorilerin `catalog.json`/`README` yeniden derlenir.

Migration olayları taksonomi diff'inden (eski `version` ↔ yeni `version`) türetilir; el ile de
(`config` veya `db-agent taxonomy edit`) tetiklenebilir.

## Sınıflandırma (categorizer) — indexing-time

Her yeni/değişen nesne için:
- **Girdi:** nesne adı + parametreler + kullandığı tablolar + `human_description` (varsa, `03`) + LLM özeti (`04`/enrichment). Özet **kalite kapısından** geçmiş olmalı (`05`); `summary_confidence: low` ise categorizer yalnızca yapısal alanlara (ad/parametre/tablo) dayanır, uydurma özete değil.
- **Görev:** ilgili taksonomiden **bir birincil** + **0..N ikincil** kategori (ve alt kategori) seç; hiçbiri uymuyorsa `diger`.
- **Çıktı:** `category` (birincil, klasör yerleşimi), `secondary_categories` (ikincil etiketler, arama/keşif), `subcategory`, `category_reason`, `confidence`.
- **Model:** ucuz/küçük LLM yeterli; offline.

**Yapışkanlık + önbellek:** Karar `(içerik_hash + taksonomi_versiyonu)` ile anahtarlanır. İkisi de değişmediyse categorizer **yeniden çalışmaz** → koşular arası kategori titremesi (flapping) olmaz.

**Pinned override:** `meta.json.pinned_category` doluysa LLM o nesneye **dokunmaz** (insan kürasyonu kalıcı). Yanlış sınıflandırmalar elle ve kalıcı düzeltilir.

**Güven:** `confidence` düşükse nesne `diger` + öneri listesine girer; `diger` oranı bir eşiği aşarsa "taksonomi boşluğu" sinyali verilir.

Determinizm: sıcaklık 0 + kategori seti enum olarak prompt'ta (serbest metin değil).

## Klasör metadata üretimi

Her anlamsal klasör için iki çıktı:

### `catalog.json` (makine-okunur)
```json
{
  "taxonomy": "code",
  "category": "teklif",
  "object_count": 142,
  "objects": [
    {"uid": "kasko-sql/KaskoDB/1234567", "alias": "dbo.SP_TEKLIF_SURELERI",
     "type": "procedure", "is_primary": true, "pinned": false,
     "summary": "Kullanıcı bazında teklif sürelerini hesaplar",
     "uses_tables": ["dbo.TEKLIF","dbo.SURE_TANIMLARI"]}
  ],
  "secondary_members": [
    {"uid": "kasko-sql/KaskoDB/7788990", "alias": "dbo.SP_RAPOR_TEKLIF",
     "primary_category": "raporlama"}     // ikincil etiketle buraya da düşer
  ],
  "common_tables": ["dbo.TEKLIF", "dbo.SURE_TANIMLARI"],
  "key_objects": ["SP_TEKLIF_OLUSTUR", "SP_TEKLIF_SURELERI"]
}
```

### `README.md` (insan-okur, LLM üretimi)
- Kategori ne iş yapar (2-3 cümle).
- En önemli nesneler + 1 satır açıklama.
- Ortak tablolar / tipik akış.
- Alt kategoriler.

README, `catalog.json`'dan deterministik iskelet + LLM ile akıcı özet olarak üretilir. Böylece "duruma göre hem yapısal hem md" isteği karşılanır: makine `catalog.json`'u, insan `README.md`'yi kullanır.

## Güncelleme davranışı

- Yeni/değişen nesne → (önbellek/pinned değilse) yeniden sınıflandır → birincil **ve** ikincil kategorilerinin `catalog.json` + `README.md`'si yeniden üretilir (sadece etkilenenler).
- Taksonomi versiyonu değişirse → etkilenen kategoriler yeniden derlenir; değişmeyenlere ve pinned'lere dokunulmaz.
- Kod ve veri taksonomileri ayrı güncellenir (tablo değişimi veri taksonomisini, SP değişimi kod taksonomisini etkiler).
