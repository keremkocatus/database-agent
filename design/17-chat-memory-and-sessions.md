# 17 — Chat Memory ve Oturumlar (Chatbot Mekanizması)

## Amaç

Kullanıcı sohbetlerini kalıcı tutmak ve çok-turlu, bağlamı koruyan bir chatbot deneyimi sunmak. Karar: **kayan pencere + rolling summary + pgvector semantik geri-çağırma**, Postgres'te saklanır. Agent (`10`) bu belleği `understand` adımında kullanır.

## Bellek stratejisi (üç katman)

1. **Kısa-dönem (pencere):** Son `N` mesaj (ör. 8–12) tam metinle prompt'a girer — anlık akış.
2. **Rolling summary:** Pencereden taşan eski mesajlar, sürekli güncellenen bir **özet**e sıkıştırılır (kullanıcının amacı, varılan kararlar, bahsedilen `uid`'ler). Token şişmesini önler, uzun sohbette süreklilik verir.
3. **Semantik geri-çağırma (long-term):** Tüm geçmiş mesajlar + özetler **pgvector**'da (BGE-M3, mevcut altyapı). Yeni soru ilgili eski parçaları **semantik** çağırır ("hani geçen hafta o teklif SP'sini konuşmuştuk").

`understand` (`10`) bu üçünü harmanlar: pencere + güncel özet + (gerekirse) geri-çağrılan parçalar → agent bağlamı. Token bütçesi (`09`) aşılırsa önce geri-çağrılanlar, sonra pencere kırpılır; özet her zaman kalır.

## Postgres şeması (tek DB ilkesi, `07`)

```sql
CREATE TABLE chat_sessions (
  id          UUID PRIMARY KEY,
  user_key    TEXT,                      -- API-key/kullanıcı (14 scope)
  title       TEXT,                      -- ilk sorudan LLM ile üretilen başlık
  scope       JSONB,                     -- bu oturumun server/db kapsamı (14)
  summary     TEXT,                      -- rolling summary
  created_at  TIMESTAMPTZ, updated_at TIMESTAMPTZ
);

CREATE TABLE chat_messages (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role        TEXT,                      -- user | assistant | tool
  content     TEXT,
  sources     JSONB,                     -- cevapta anılan uid'ler (10)
  trace_id    TEXT,                      -- observability (16)
  tokens      INT, created_at TIMESTAMPTZ
);

CREATE TABLE chat_memory_embeddings (    -- semantik geri-çağırma
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  message_id  BIGINT,                    -- ya da özet parçası
  kind        TEXT,                      -- 'message' | 'summary'
  content     TEXT,
  embedding   vector(1024)
);
CREATE INDEX ON chat_memory_embeddings USING hnsw (embedding vector_cosine_ops);
```

## Özetleme (summarization) mekanizması

- **Tetik:** pencere mesaj sayısı/token eşiği aşılınca veya oturum kapanınca.
- **Nasıl:** Eski mesajlar + mevcut özet → LLM (`enricher`/ucuz rol, `09`) → güncellenmiş özet (yapısal: amaç, kararlar, anahtar `uid`'ler, açık sorular). Schema'lı çıktı + doğrulama (`09`).
- **Maliyet:** Offline değil ama seyrek; önbellek değil (bağlam değişken). Determinizm için temp düşük.
- Özet de embed edilir → uzun oturumda semantik geri-çağırmaya girer.

## Chatbot akışı (query-time, `10`/`12` ile)

```
kullanıcı mesajı (session_id)
  → understand: pencere + özet + semantik geri-çağrılan parçalar + koreferans çözümü
  → agent ReAct (araçlar, retrieval, grounding)
  → cevap + kaynak uid'ler
  → kalıcılaştır: user+assistant mesajı yaz, embed et;
    eşik aşıldıysa rolling summary güncelle
```

- **Başlık:** İlk mesajdan kısa başlık üretilir (oturum listesi için).
- **Çok-turlu:** "onu/bunu" → önceki `sources` `uid`'lerine çözülür (`10`).
- **Akış (streaming):** SSE ile adımlar + cevap (`12`).

## API / CLI (12 ile)

```
POST /v1/sessions                 # yeni oturum  → {session_id, title?}
GET  /v1/sessions                 # kullanıcının oturumları (kapsam-filtreli)
GET  /v1/sessions/{id}            # mesaj geçmişi + özet
POST /v1/ask {session_id, ...}    # mesaj gönder (10) — oturuma yazar
DELETE /v1/sessions/{id}          # oturumu sil (retention/gizlilik)
```
CLI: `db-agent ask --session <id> "..."`, `db-agent sessions [list|show|rm]`.

## Çok-kullanıcı, gizlilik, retention

- **İzolasyon:** Oturum `user_key`'e bağlı; kullanıcı yalnızca kendi oturumlarını görür. Oturum `scope`'u (`14`) sorgu kapsamını da sınırlar — sohbet, kullanıcının izinli server/db'leri dışına çıkamaz.
- **Eşzamanlı çok-kullanıcı (`20`):** Oturum durumu Postgres'te, agent state sorgu-bazlı in-memory (`10`) → oturumlar birbirini etkilemez; birden fazla kişi aynı anda sohbet edebilir. Eşzamanlı `/ask` sayısı `query.max_concurrent_chats` slot'u kadar paralel; aşılırsa kuyruğa alınır (`queued` SSE), kapasite tükenirse `503`+`capacity` — **sistem kilitlenmez, kullanıcı bilgilendirilir** (`20`).
- **Dışlama + per-user görünürlük:** Sohbet belleği de dışlama (`14`) ve **per-user `deny`** (`14` §3.1) kurallarına uyar — kullanıcıya kapalı `uid` ne cevapta ne geçmişte tutulur.
- **Retention (`01`/`16`):** Oturum/mesaj saklama penceresi yapılandırılır; kullanıcı kendi oturumunu silebilir. Mesaj içerikleri maskeleme kurallarına tabi.
- **Audit:** Her mesaj `trace_id` ile observability'ye (`16`) bağlı.

## Etkileşim & geri-bildirim
Responsive netleştirme (1-3 soru), bulgular+gerekçe+onay döngüsü, onaydan öğrenme ve adım-adım streaming **`18`**'de tanımlı. Netleştirme cevapları ve onay sonuçları bu oturumun belleğine/`search_feedback`'e yazılır.

## Neden bu strateji?
- **Pencere:** anlık doğallık, ucuz.
- **Rolling summary:** uzun sohbette token patlamasını önler, süreklilik verir.
- **Semantik geri-çağırma:** "çok önce konuştuğumuz spesifik nesne"yi yakalar — sadece pencere/özetin kaçıracağı detay. Mevcut pgvector altyapısını yeniden kullanır, ek servis yok.
