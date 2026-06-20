-- 0001 — Postgres uzantıları (design/07, /08). pgvector + pg_trgm.
-- Not: vektör kolonları M4'te eklenecek; uzantı şimdiden hazır (init/doctor doğrular).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
