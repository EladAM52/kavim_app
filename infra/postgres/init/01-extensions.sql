-- Runs once, on first initialization of an empty data volume.
-- These extensions are prerequisites for the schema, so they are created here
-- rather than in a migration — Alembic should not need superuser privileges.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email columns
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- trigram search (Hebrew has no stemmer)
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- mixed btree/GIN indexes on JSONB
