-- 0003 — Enrich/Categorize/Index (M4, design/06, /07, /09).
-- objects'e anlamsal alanlar; embeddings (dense+sparse) tablosu; llm_cache (offline önbellek).

ALTER TABLE objects
    ADD COLUMN IF NOT EXISTS subcategory          TEXT,
    ADD COLUMN IF NOT EXISTS secondary_categories TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS data_category        TEXT,
    ADD COLUMN IF NOT EXISTS pinned               BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pinned_category      TEXT,
    ADD COLUMN IF NOT EXISTS summary_confidence   TEXT,        -- ok | low (design/05 kalite kapısı)
    ADD COLUMN IF NOT EXISTS fail_reason          TEXT,        -- state='failed' ise (design/09)
    ADD COLUMN IF NOT EXISTS search_name          TEXT;        -- Türkçe-fold normalize ad (design/07)

CREATE INDEX IF NOT EXISTS ix_objects_scope_cat
    ON objects (server, database, object_kind, category);
CREATE INDEX IF NOT EXISTS ix_objects_secondary
    ON objects USING gin (secondary_categories);
CREATE INDEX IF NOT EXISTS ix_objects_search_name_trgm
    ON objects USING gin (search_name gin_trgm_ops);

-- Embedding kartları (design/07): kind = card | body | table | category.
-- Ham SQL gövdesi DİSKTE; burada yalnızca kart içeriği + vektörler.
-- NOT: dense (vector) M4'te birincil arama kolu. BGE-M3 öğrenilmiş sparse şimdilik JSONB tutulur;
-- sparsevec + HNSW materyalizasyonu M5 retrieval'da (canlı DB'de doğrulanabildiğinde) yapılır (design/08).
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id        BIGSERIAL PRIMARY KEY,
    uid             TEXT NOT NULL REFERENCES objects (uid) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                 -- card | body | table | category
    content         TEXT NOT NULL,
    embedding       vector(1024),                  -- dense (design/07: tam float32)
    sparse_json     JSONB,                         -- BGE-M3 sparse (token_id→ağırlık); cloud'da NULL
    embedding_model TEXT NOT NULL,                 -- aktif-set damgası (design/07 re-embed swap)
    dim             INT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_emb_dense_hnsw
    ON embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_emb_active_set
    ON embeddings (uid, embedding_model);          -- re-embed swap / incremental
CREATE INDEX IF NOT EXISTS ix_emb_kind
    ON embeddings (kind);

-- Offline görev önbelleği (design/09): (prompt_hash + model_id) → yanıt.
CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,                   -- sha256(prompt + model_id + role)
    model_id   TEXT NOT NULL,
    response   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
