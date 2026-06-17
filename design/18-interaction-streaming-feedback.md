# 18 — Etkileşim, Streaming ve Geri-Bildirim

## Amaç

Sohbeti **responsive** kılmak: gerektiğinde netleştirme sorusu, bulduktan sonra onay isteme, onaydan öğrenme ve adım-adım streaming. `10` (agent), `12` (serving), `15` (eval), `17` (chat) ile birlikte çalışır.

## 1) Sınırlı uyarlamalı netleştirme (clarification)

Karar: **gerekirse 1-3 soru, teker teker.**
- **Ne zaman:** `understand` (`10`) niyeti/kapsamı düşük güvenle belirsiz bulursa (ör. hangi DB? kod mu veri mi? "rapor" hangi anlamda?). Yeterince netse **hiç sormaz**, arar.
- **Nasıl:** `ask_user(question, options?)` aracı bir soru sorar (gerekirse şık önerir), cevabı bekler, gerekirse 1-2 takip daha — **sert üst sınır 3** (sonra en iyi tahminle ilerler, "varsayımım şu" diyerek).
- **Streaming:** soru bir `clarification` olayı olarak akar; UI'da hızlı seçim çipleri (`options`) gösterilebilir.
- **Bellekle:** netleştirme cevapları oturum belleğine yazılır (`17`); aynı şeyi tekrar sormaz.

## 2) Bulgular + gerekçe + onay döngüsü

Karar: **arama sonrası bulguları + neden'i sun, tek-dokun onay iste.**

Akış:
```
agent arar/okur → aday(lar)ı + her biri için kısa gerekçe (why, 08) sunar
   → "Bunu mu arıyordun?"  [✓ Evet, buydu] [↻ Hayır, şunu kastettim…] [+ Detay]
   → kullanıcı yanıtı:
       ✓ → confirmed: (sorgu → onaylı uid'ler) pozitif örnek kaydedilir
       ↻ → düzeltme metni understand'e geri beslenir, yeni tur (responsive)
```
- Onay **hafif** ve her cevabın sonunda (yüksek güvende de) — ama akışı kesmez; kullanıcı yok sayıp devam edebilir.
- "Hayır" → kullanıcının düzeltmesi yeni netleştirme/aramaya döner (kapalı responsive döngü).
- Onay olayları `confirmation_request` / `feedback` olarak streaming protokolünde taşınır.

## 3) Onaydan öğrenme (feedback → 3 kullanım)

Onaylanan `(sorgu, uid[])` pozitif çifti `feedback` tablosuna yazılır ve **üç yerde** kullanılır:

```sql
CREATE TABLE search_feedback (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID, user_key TEXT,
  query       TEXT, query_embedding vector(1024),
  confirmed_uids TEXT[], rejected_uids TEXT[],
  verdict     TEXT,            -- confirmed | corrected | rejected
  scope       JSONB, created_at TIMESTAMPTZ
);
```

1. **Retrieval boost (öğrenilen sinyal, `08`):** Yeni sorgu, geçmiş onaylı sorgulara semantik olarak yakınsa, onların `confirmed_uids`'i adaylara **hafif boost** alır (RRF sonrası, rerank öncesi). Aşırı kilitlenmeyi önlemek için boost sınırlı + zamanla söner; rejected_uids hafif ceza.
2. **Few-shot / önbellek:** Çok benzer sorgu tekrar gelirse onaylı sonuç hızlı yol olarak sunulabilir (düşük eşik + "önceden onaylanmıştı" notu); ayrıca few-shot örnek olarak agent prompt'una girebilir.
3. **Altın set (`15`):** Onaylı çiftler değerlendirme setini **gerçek kullanımla** büyütür (insan küratör onayından geçirilerek); recall@k/MRR ölçümü canlı veriye dayanır.

**Güvenceler:** Boost yalnızca aynı `scope` (`14`) içinde geçerli; dışlanan `uid` asla öğrenilmez; feedback maskeleme/retention kurallarına tabi (`14`/`16`). Negatif (rejected) sinyaller de saklanır ama daha temkinli kullanılır.

## 4) Streaming olay protokolü (tipli SSE)

Karar: **tipli olay akışı** — UI adım-adım render eder (kodlama araçları gibi). `/ask` (`12`) `Accept: text/event-stream` ile bu olayları yayar; aynı olaylar trace'e de gider (`16`).

| Olay | Yük (payload) | UI render |
|---|---|---|
| `understanding` | `{intent, rewritten_query}` | "Soruyu anlıyorum…" |
| `clarification` | `{question, options?}` | Soru + seçim çipleri (1) |
| `plan` | `{steps[]}` | Hafif plan listesi |
| `tool_call` | `{tool, args, step_id}` | "🔍 search_objects(...)" satırı |
| `tool_result` | `{step_id, summary, count}` | Sonuç özeti (katlanabilir) |
| `token` | `{delta}` | Cevap metni akar (token-token) |
| `sources` | `{items:[{uid,alias,why}]}` | Kaynak kartları |
| `confirmation_request` | `{candidates[], reasons[]}` | "Bunu mu arıyordun?" + onay (2) |
| `usage` | `{tokens, latency_ms, tools}` | (debug/observability) |
| `error` | `{message, recoverable}` | Hata satırı |
| `done` | `{trace_id, confidence, note}` | Bitti |

**İlkeler:**
- Her olay **tek satır JSON** (SSE `data:`), `seq` + `trace_id` ile sıralı/izlenebilir.
- Sözleşme **transport-agnostik:** SSE bugün; ileride WebSocket'e aynı olay şeması taşınır.
- `tool_result` **özet** taşır (büyük SQL değil — `09` token bütçesi); UI isterse detay uçtan çeker.
- Olay protokolü **şimdiden** tanımlı; UI olmadan da CLI `--verbose` (`12`) aynı olayları metin olarak basar → çekirdek hazır, UI sonradan zahmetsiz bağlanır.

## Etkileşim döngüsü (özet)

```
soru → [understanding] → (belirsizse [clarification]×1-3)
     → [plan] → ([tool_call]→[tool_result])* → [token]* + [sources]
     → [confirmation_request] → kullanıcı ✓/↻
        ✓ → search_feedback (+ boost/few-shot/altın-set)
        ↻ → düzeltme → understand → yeni tur
     → [done]
```

## Tasarım notu
Bu mekanizma agent'ın (`10`) **dış kabuğudur**: ReAct döngüsü değişmez, üzerine etkileşim (clarify/confirm) + gözlemlenebilir olay yayımı eklenir. Feedback, retrieval'ı (`08`) zamanla iyileştiren tek **çevrimiçi öğrenme** noktasıdır; model fine-tune yok, sadece veri sinyali (basit, güvenli, tersine alınabilir).
