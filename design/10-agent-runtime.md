# 10 — Agent Runtime (Custom ReAct Loop)

## Amaç

Kullanıcının doğal dil sorusunu, araçları çağırarak çok-adımlı çözen bir agent. Karar: **custom ReAct loop** — framework yok, tam kontrol.

## Neden custom?

- Döngü dar ve iyi tanımlı: düşün → araç seç → çalıştır → gözlemle → tekrar / bitir.
- LangGraph/CrewAI güçlü ama bizim ihtiyacımız için fazladan soyutlama + bağımlılık.
- Provider-agnostik katmanla (`09`) doğrudan oturur; tool-call zaten normalize.
- "Minimum bağımlılık, tam kontrol" felsefesiyle birebir.

## Döngü

```
state = { question, history, intent, scratch=[], shortlist=[], budget }

# (0) Anlama: niyet sınıflandırma + sorgu yeniden yazma (08; tek LLM adımı)
#     - Türkçe eşanlamlı/kısaltma genişlet; çok-turlu ise "onu/bunu" → önceki uid çöz
#     - belirsiz/çok geniş + düşük güven → TEK netleştirme sorusu döndür (aşağıdaki karar)
#     - DÜŞÜK GÜVENDE rewrite'ı kullanıcıya göster (2.4): "şunu mu kastettin: …" (onay/düzelt)
intent, rewritten = understand(question, history)

while not budget.exceeded():                 # max_iter + max_tokens + duvar-saati
    plan = LLM.chat(system_prompt, messages, tools=TOOLSPECS)
    if plan.tool_calls:
        for call in parallel_or_serial(plan.tool_calls):   # provider destekliyorsa paralel
            messages.append(tool_result(dispatch(call)))
    elif "finish":
        return verify_and_emit(plan.answer)   # grounding doğrulama (aşağıda)
return summarize(shortlist)                   # bütçe aşıldıysa eldekiyle dürüst özet
```

- **Planlama:** saf ReAct; çok-hop sorularda system prompt **hafif bir plan** teşvik eder (ayrı planlama turu yok).
- **Paralel araç:** native paralel tool-call varsa bağımsız çağrılar (ör. kod + tablo araması) paralel; yoksa seri.
- **Durdurma:** `finish`, ya da bütçe (iterasyon/token/saat), ya da art arda yararsız adım sayacı.
- **State:** sorgu-bazlı in-memory; konuşma geçmişi API oturumunda (`12`), çok-turlu bağlam `understand`'da çözülür.

## Veritabanı kapsamı çözümü (çok-DB — karar: DB-başına izole arama)

Karar: **Sistem çok-DB ilişkilendirme/birleştirme yapmaz; her DB kendi içinde keşfedilir ve aranır.**
Cross-DB *bağımlılık kenarları* (`02`/`04`) keşif için kurulur ama sorgu, **tek bir hedef DB**
kapsamında çalışır. Agent hedef DB'yi şöyle belirler:

1. **Kullanıcı belirttiyse:** `server`/`database` filtresi doğrudan araçlara geçer (`search_objects`).
2. **Belirtmediyse → agent SORAR:** `ask_user("Hangi veritabanında arayayım?", options=[en olası DB'ler])`.
   Bu, çok-DB belirsizliğinde **birincil** yoldur (tek netleştirme sorusu, `18`).
3. **Kullanıcı bilmiyorsa → sıralı fallback (karar):** Agent "bilmiyorum/hepsinde ara" yanıtında,
   DB'leri **en olası → en az olası** sırayla dener:
   - Olasılık sırası: sorgu embedding'inin her DB'nin **kategori/özet kartlarına** (`07` `kind='category'`)
     yakınlığı + ad/lexical sinyali ile hesaplanır (ucuz ön-eleme; hangi DB bu konuyu içeriyor).
   - En olası DB'de tam arama → eşik geçen sonuç bulunursa **durur ve sunar.**
   - Bulamazsa sıradaki DB'ye geçer; her geçişte kısa ilerleme sinyali (`18` `tool_result`).
   - **"Uzun sürebilir" uyarısı:** Birden fazla DB taranacaksa kullanıcıya baştan
     `{note: "kapsam belirsiz, en olası DB'lerden başlayarak sırayla arıyorum, biraz uzun sürebilir"}`
     bilgisi (SSE) verilir.
   - Sert sınır: en fazla `max_fallback_dbs` (config, ör. 5) DB denenir; sonra "şu DB'lerde bulundu /
     hiçbirinde net sonuç yok, lütfen DB belirt" der.
4. **Bitmemiş DB'den soru (1.5):** Hedef DB henüz hiç indekslenmemiş/bootstrap tamamlanmamışsa agent
   sessiz boş dönmez: "bu DB henüz katalogda hazır değil (indeksleniyor/onay bekliyor)" der (`16` freshness).

Bu döngü, "tek DB net cevap" hedefiyle "kullanıcı DB bilmese de bulmaya çalış" esnekliğini dengeler;
çok-DB sonuç *karıştırma/sıralama* karmaşıklığına hiç girilmez (kapsam dışı, `REVIEW-gap-analysis` 2.2).

## Araç seti (tools)

```python
search_objects(query, top_k=8, server=None, database=None, object_kind=None,
               category=None, types=None, writes_table=None, reads_table=None) -> SearchResponse
    # hybrid + rerank (08). uid/alias + skor + why + note(bulunamadı/düşük güven).

read_object(uid, full=False) -> str
    # full=False → özet (parametre/tablo/özet, human_description öncelikli). full=True → ham SQL.
    # büyük SQL bütçeye göre kırpılır/özetlenir (09 token bütçesi).

get_dependencies(uid) -> {calls, reads_tables, writes_tables}     # read/write ayrı (04)
get_dependents(uid)   -> {called_by, read_by, written_by}         # etki analizi (04)

search_tables(query, top_k=8) -> list[TableResult]    # tablo + view (05)
describe_table(uid) -> {columns, pk, fks, checks, read_by, written_by}

get_history(uid) -> [{when, old_hash, new_hash, run_id}]   # changelog/.prev.sql (03)

list_scope() -> {servers, databases, categories}    # "neyi arayabilirim" — kapsam keşfi

add_to_shortlist(uid, reason)            # "bunu beğendim, sebebi şu"
ask_user(question)                       # TEK netleştirme sorusu (aşağıdaki karar)
finish(answer, sources)                  # döngüyü bitir; kaynaklar zorunlu
```

### Tasarım notu — neden `read_object` özet-öncelikli?
2000+ nesnede her adayın tam SQL'ini okumak context'i şişirir. Agent önce **kart/özet** görür (`04`+`06`), sadece kararı etkileyecekse `full=True` ile ham SQL'e iner. Bu, "agent'a ham SQL yerine parse edilmiş özet ver" ilkesinin uygulaması.

## System prompt iskeleti (özet)

- Rol: "Sen bir MSSQL kod tabanı keşif asistanısın. Soruları, verilen araçlarla kanıt toplayarak cevaplarsın."
- Kurallar: önce ara, sonra oku; emin değilsen `get_dependencies` ile doğrula; **uydurma — sadece okuduğun nesnelere dayan**; `08` `note=no_match` ise "bulunamadı" de; cevapta **kaynak nesneleri (`uid`/alias)** ver; **kullanıcının dilinde** cevapla.
- Çıktı: kısa cevap + dayanak nesneler (`uid`/alias) + güven notu.
- Kapsam: kullanıcı belirttiyse `server/database/object_kind` filtresini araçlara geçir; çok geniş + belirsizse `ask_user` ile **tek** netleştirme sorusu sor.

## Cevap formatı

```
Cevap: Teklif süresini hesaplayan ana SP: dbo.SP_TEKLIF_SURELERI.
       Kullanıcı + tarih aralığı alır, SURE_TANIMLARI ve KULLANICI_YETKI'yi okur, TEKLIF_LOG'a yazar.
Kaynaklar:
  - kasko-sql/KaskoDB/1234567 (dbo.SP_TEKLIF_SURELERI, procedure)  — doğrudan eşleşme
  - çağırdığı: dbo.SP_KULLANICI_YETKI_KONTROL
Güven: yüksek (ad + özet + bağımlılık doğrulandı)
```
Kaynak gösterimi zorunlu — sistem "şu nesne, şu sebeple" demeli (Claude Code'un repo davranışı gibi: bul, oku, gerekçelendir).

## Grounding doğrulama (cevap öncesi)

Karar: **ucuz yapısal + düşük-güvende LLM.**
- **Her zaman (ucuz, LLM'siz):** `finish`'teki her kaynak `uid` gerçekten **var mı** ve bu sorguda **okundu/getirildi mi** kontrol edilir. Uydurulan/okunmamış `uid` reddedilir → agent'a "kaynağını göster" gözlemi döner.
- **Sadece düşük güvende:** skor/eşik düşükse bir ek LLM grounding kontrolü ("cevap, anılan nesnelerce destekleniyor mu?"). Yüksek güvende bu adım atlanır → gereksiz maliyet yok.

## Hata ve kenar durumlar

- Araç hatası → agent'a hata gözlemi döner, alternatif dener (döngü kilitlenmez).
- Sonuç yoksa (`note=no_match`) → kapsamı gevşetir ya da "bulunamadı, şu yakın adaylar" der.
- Çok geniş/belirsiz soru → `ask_user` ile netleştirme; sınırlı uyarlamalı **1-3 soru** (sert üst sınır 3), sonra "varsayımım şu" diyerek devam (`18`).
- Cevap sonrası **bulgular+gerekçe+onay** döngüsü ve onaydan öğrenme (boost/few-shot/altın-set) `18`'de tanımlı; agent ReAct döngüsünün dış kabuğudur.
- Çok-turlu: takip sorusunda referans (`onu/bunu`) önceki cevabın `uid`'ine çözülür.
- **Dışlama (görünmez):** Agent dışlanan nesneleri hiç görmez (indekste yok + retrieval filtresi, `14`). Kullanıcı doğrudan adıyla sorsa bile sistem o nesneyi tanımaz; "böyle bir şey var ama erişemezsin" demez — yokmuş gibi davranır (varlık ifşası yok).
- **Güvenilmez içerik:** Tool sonuçları (SP gövdesi/yorum) **veri** olarak işlenir, talimat olarak değil; içindeki "şunu yap/yoksay" gibi metinler emir kabul edilmez (`14` prompt-injection).
- **Tazelik/kapsam ifşası:** Cevap, kaynak DB'nin **son sync tarihine** dayanır; ilgili DB degraded/eski ise cevaba "bu bilgi <tarih> itibarıyla" notu eklenir. Sorulan kapsam **hiç indekslenmemişse** (ör. pending/onaysız DB) agent "bu DB henüz katalogda değil" der — sessizce boş/yanlış cevap vermez. Tazelik verisi `16` `index_freshness`'ten gelir.

## Gözlemlenebilirlik

- Her sorguda: kullanılan araçlar, çağrı sayısı, iterasyon, latency, seçilen nesneler → trace log (debug + kalite analizi).
- `--verbose` CLI modunda ReAct adımları ekrana basılır (Claude Code benzeri şeffaflık).

## Sınırlar
- Multi-hop bağımlılık tipik 2-3 adımda biter; `MAX_ITER` bunu rahat karşılar.
- Yazma/çalıştırma yok — agent sadece **okur ve açıklar** (kaynak DB salt-okunur).
