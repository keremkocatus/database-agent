-- 0002 — Katalog çekirdeği (design/01, /04, /05).
-- objects: tüm katalog düğümleri (SP/View/Function/Trigger + tablo). Ham SQL gövdesi DİSKTE,
--          burada yalnızca yapısal meta (JSONB) + kart alanları (design/19 risk notu).
-- edges:   bağımlılık grafiği (calls/reads/writes). Tek DB ilkesi; ayrı graph DB yok (design/04).
-- runs:    sync run özeti (design/01 platform katmanı).

CREATE TABLE IF NOT EXISTS objects (
    uid               TEXT PRIMARY KEY,                 -- server/database/object_id (kalıcı)
    alias             TEXT NOT NULL,                    -- server/database/schema/name (okunur)
    server            TEXT NOT NULL,
    database          TEXT NOT NULL,
    schema            TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,                    -- procedure|view|function|trigger|table
    object_id         BIGINT NOT NULL,
    object_kind       TEXT,                             -- table|view (tablolar için)
    modify_date       TIMESTAMPTZ,
    content_hash      TEXT,
    state             TEXT NOT NULL DEFAULT 'extracted',-- extracted→parsed→…→indexed | parse_error
    flags             JSONB NOT NULL DEFAULT '{}'::jsonb,
    meta              JSONB NOT NULL DEFAULT '{}'::jsonb,-- parse çıktısı / tablo sözlüğü
    human_description TEXT,
    summary           TEXT,                             -- M4 (LLM)
    category          TEXT,                             -- M4 (LLM)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_objects_alias ON objects (alias);
CREATE INDEX IF NOT EXISTS ix_objects_scope ON objects (server, database);
CREATE INDEX IF NOT EXISTS ix_objects_type ON objects (type);
CREATE INDEX IF NOT EXISTS ix_objects_name_trgm ON objects USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS edges (
    id           BIGSERIAL PRIMARY KEY,
    src_uid      TEXT NOT NULL REFERENCES objects (uid) ON DELETE CASCADE,
    dst_uid      TEXT NOT NULL REFERENCES objects (uid) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                         -- calls|reads|writes
    via_synonym  BOOLEAN NOT NULL DEFAULT false,
    is_updated   BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (src_uid, dst_uid, kind)
);

CREATE INDEX IF NOT EXISTS ix_edges_src ON edges (src_uid);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges (dst_uid);

CREATE TABLE IF NOT EXISTS runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    server       TEXT NOT NULL,
    database     TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running',       -- running|ok|degraded|error
    counts       JSONB NOT NULL DEFAULT '{}'::jsonb,    -- added/changed/removed/unchanged/parse_error
    errors       JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_runs_scope ON runs (server, database, started_at DESC);
