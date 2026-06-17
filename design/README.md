# Tasarım Dokümanları — MSSQL Agentic Katalog Sistemi

Bu klasör, sistemin tasarım kararlarını kategori kategori kayıt altına alır. Her doküman bir karar kümesini, değerlendirilen alternatifleri ve seçim gerekçesini içerir. Kod yazımı bu kararlara dayanır.

## Okuma sırası

| # | Doküman | Konu |
|---|---|---|
| 00 | [Genel Bakış](00-overview.md) | Hedefler, kapsam, ilkeler, karar özeti, sözlük |
| 01 | [Mimari](01-architecture.md) | Bileşenler, veri akışı (indexing & query), katman bağımsızlığı |
| 02 | [Bağlantı ve Keşif](02-connection-and-discovery.md) | Çok-sunucu YAML+.env config, DB/şema/nesne keşfi |
| 03 | [Extraction ve Store](03-extraction-and-store.md) | Nesne çekme, lokal store düzeni, modify_date+hash değişim tespiti |
| 04 | [Parsing ve Bağımlılık](04-parsing-and-dependencies.md) | sqlglot + server-side bağımlılık, yapısal metadata, graph |
| 05 | [Tablo Sözlüğü](05-table-dictionary.md) | Data dictionary, ilişkiler, LLM açıklama, tablo keşfi |
| 06 | [Kategorizasyon ve Klasörleme](06-categorization-and-foldering.md) | Hibrit foldering, DB-başına taksonomi, klasör metadata |
| 07 | [Embedding ve İndeksleme](07-embedding-and-indexing.md) | BGE-M3, object card + chunk, pgvector şeması |
| 08 | [Retrieval ve Reranking](08-retrieval-and-reranking.md) | Hybrid + RRF + cross-encoder reranker |
| 09 | [LLM Provider Katmanı](09-llm-provider-layer.md) | İnce custom adapter, lokal + cloud provider'lar |
| 10 | [Agent Runtime](10-agent-runtime.md) | Custom ReAct loop, araç seti, cevap formatı |
| 11 | [Scheduling ve Self-Update](11-scheduling-and-selfupdate.md) | APScheduler + CLI, incremental sync akışı |
| 12 | [Serving: API ve CLI](12-serving-api-cli.md) | FastAPI REST + Typer CLI |
| 13 | [Tech Stack ve Yol Haritası](13-tech-stack-and-roadmap.md) | Bağımlılıklar, Clean Architecture repo iskeleti, M0–M8 milestone'lar |
| 14 | [Güvenlik ve Gizlilik](14-security-and-privacy.md) | Sır maskeleme, prompt-injection guardrail, serving erişim/kapsam |
| 15 | [Test ve Değerlendirme](15-testing-and-eval.md) | Unit/entegrasyon/sözleşme testleri, retrieval altın seti, CI |
| 16 | [Observability ve Monitoring](16-observability-and-monitoring.md) | Prometheus+Grafana+OTel+JSON log, metrik/trace/dashboard/alert |
| 17 | [Chat Memory ve Oturumlar](17-chat-memory-and-sessions.md) | Pencere+özet+semantik bellek, oturum/şema, chatbot akışı |
| 18 | [Etkileşim, Streaming ve Geri-Bildirim](18-interaction-streaming-feedback.md) | Netleştirme, onay döngüsü, onaydan öğrenme, tipli SSE olay protokolü |
| 19 | [Deployment ve Operasyon](19-deployment-and-operations.md) | docker-compose, init/doctor, bootstrap, yedek/restore (DR), maliyet tavanı, quickstart |

## Tek bakışta kararlar

- **Mimari:** Clean Architecture — `src/` altında `api/cli/worker · application · domain · infrastructure`; AI = `application/agent` slice (yeni katman yok). Outfit deseniyle tutarlı.
- **Topoloji:** API + Worker ayrık, **Postgres job-queue**; otorite alana-bölünmüş (disk içerik / Postgres indeks) + reconciler.
- **Provider-agnostik** LLM/embedding/reranker (lokal vLLM/Ollama + cloud Vertex/OpenAI/Anthropic), **ince custom adapter** (= infra port'ları).
- Embedding: **BGE-M3 (dense+sparse)** swappable; Index: **PostgreSQL pgvector + pg_trgm** (vektör+sparse+metadata+graph tek DB).
- DB erişimi: **SQLAlchemy async engine + ham SQL**, DB-tarafı RPC yok; **SQL-dosya migration**.
- Retrieval: **dense+sparse+trigram → RRF+tam-ad boost → uyarlamalı rerank → eşik**; Orchestration: **custom ReAct** + niyet + grounding.
- Kapsam: **SP+View+Function+Trigger + tablo/view sözlüğü + synonym/cross-DB/UDT**; salt-okunur kaynak.
- Değişim tespiti: **modify_date + hash**, **hibrit kimlik (uid+alias)**, changelog+prev; incremental **per-object job**.
- Klasörleme: hibrit, **kod + veri ayrı taksonomi**, birincil+ikincil, pinned override.
- Repo'dan bağımsız, **git'siz** lokal store; **çok-sunucu/çok-DB** keşfi (yeni DB → onay kapısı).
- Güvenlik: **dışlama (exclusion)** ile kritik tablo/SP tamamen görünmez; prompt-injection guardrail; serving scope auth (`14`).
- Observability: **Prometheus+Grafana+OTel+JSON log** (`16`); Chatbot: **kalıcı oturum + pencere/özet/semantik bellek** (`17`); Etkileşim: **netleştirme + onay/feedback + tipli SSE streaming** (`18`).
- Otorite: disk=içerik; Postgres indeksi atılabilir ama **feedback/chat/runs otoriter → yedeklenir**; DR + bootstrap + docker-compose + init/doctor (`19`). Açık-kaynak default modeller **Apache-lisanslı** (Qwen/BGE).

## Durum
Tasarım fazı tamamlandı (00–13 elden geçti). Sonraki adım: açık soruların (bkz. `13` sonu) netleştirilmesi ve M0 iskeletinin kurulması.
