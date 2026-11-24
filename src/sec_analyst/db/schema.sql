-- Dual-Store Schema: Quantitative XBRL + Qualitative Hybrid RAG (pgvector + FTS)
-- Target: PostgreSQL 16+ with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. SEC Filings Registry (Source of Truth for Provenance & Amendment Resolution)
CREATE TABLE IF NOT EXISTS filings (
    accession_number TEXT PRIMARY KEY,
    cik              TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    company_name     TEXT NOT NULL,
    form_type        TEXT NOT NULL CHECK (form_type IN ('10-K', '10-K/A', '10-Q', '10-Q/A')),
    fiscal_year      INTEGER NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    fiscal_period    TEXT NOT NULL DEFAULT 'FY' CHECK (fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4')),
    filing_date      DATE NOT NULL,
    period_end_date  DATE NOT NULL,
    primary_document TEXT NOT NULL,
    filing_url       TEXT NOT NULL,
    is_amendment     BOOLEAN NOT NULL DEFAULT FALSE,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_filings_ticker_fy
    ON filings (ticker, fiscal_year, filing_date DESC);

-- 2. Quantitative Store: Structured XBRL Facts
CREATE TABLE IF NOT EXISTS xbrl_facts (
    id                BIGSERIAL PRIMARY KEY,
    accession_number  TEXT NOT NULL REFERENCES filings(accession_number) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    fiscal_period     TEXT NOT NULL DEFAULT 'FY' CHECK (fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3', 'Q4')),
    period_end_date   DATE NOT NULL,
    raw_tag           TEXT NOT NULL,
    canonical_concept TEXT,
    value             NUMERIC NOT NULL,
    unit              TEXT NOT NULL,
    decimals          INTEGER,
    frame             TEXT,
    UNIQUE (accession_number, raw_tag, period_end_date, fiscal_period, unit)
);

CREATE INDEX IF NOT EXISTS idx_xbrl_lookup
    ON xbrl_facts (ticker, fiscal_year, fiscal_period, canonical_concept, unit);

CREATE INDEX IF NOT EXISTS idx_xbrl_raw_tag
    ON xbrl_facts (ticker, fiscal_year, raw_tag);

-- Read-only view resolving restatements/amendments by preferring the latest filing_date
CREATE OR REPLACE VIEW v_latest_xbrl_facts AS
SELECT DISTINCT ON (xf.ticker, xf.fiscal_year, xf.fiscal_period, xf.raw_tag, xf.unit)
    xf.id,
    xf.accession_number,
    xf.ticker,
    xf.fiscal_year,
    xf.fiscal_period,
    xf.period_end_date,
    xf.raw_tag,
    xf.canonical_concept,
    xf.value,
    xf.unit,
    xf.decimals,
    xf.frame,
    f.form_type,
    f.filing_date,
    f.filing_url,
    f.is_amendment
FROM xbrl_facts xf
JOIN filings f ON f.accession_number = xf.accession_number
ORDER BY
    xf.ticker,
    xf.fiscal_year,
    xf.fiscal_period,
    xf.raw_tag,
    xf.unit,
    f.filing_date DESC,
    f.is_amendment DESC;

-- 3. Qualitative Store: Parent Paragraphs (Full Context for Synthesis & Audit)
CREATE TABLE IF NOT EXISTS narrative_parents (
    id                BIGSERIAL PRIMARY KEY,
    accession_number  TEXT NOT NULL REFERENCES filings(accession_number) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    item_section      TEXT NOT NULL CHECK (item_section IN ('Item 1', 'Item 1A', 'Item 1C', 'Item 2', 'Item 7', 'Item 8')),
    section_title     TEXT,
    parent_index      INTEGER NOT NULL,
    content           TEXT NOT NULL,
    tsv               TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (accession_number, item_section, parent_index)
);

CREATE INDEX IF NOT EXISTS idx_parents_filter
    ON narrative_parents (ticker, fiscal_year, item_section);

CREATE INDEX IF NOT EXISTS idx_parents_tsv
    ON narrative_parents USING GIN (tsv);

-- 4. Qualitative Store: Child Chunks (Precision Retrieval via Vector + FTS)
CREATE TABLE IF NOT EXISTS narrative_chunks (
    id                BIGSERIAL PRIMARY KEY,
    parent_id         BIGINT NOT NULL REFERENCES narrative_parents(id) ON DELETE CASCADE,
    accession_number  TEXT NOT NULL REFERENCES filings(accession_number) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    item_section      TEXT NOT NULL CHECK (item_section IN ('Item 1', 'Item 1A', 'Item 1C', 'Item 2', 'Item 7', 'Item 8')),
    chunk_index       INTEGER NOT NULL,
    content           TEXT NOT NULL,
    embedding         VECTOR(1024),
    tsv               TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (parent_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_filter
    ON narrative_chunks (ticker, fiscal_year, item_section);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv
    ON narrative_chunks USING GIN (tsv);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON narrative_chunks USING hnsw (embedding vector_cosine_ops);
