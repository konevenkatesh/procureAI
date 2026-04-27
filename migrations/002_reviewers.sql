-- ─────────────────────────────────────────────────────────────────────────────
-- Reviewer audit + multi-reviewer support.
-- Adds two tables:
--   reviewers      — one row per person who signs up via the portal
--   rule_reviews   — one row per action (approve / reject / modify / skip)
--
-- The existing `rules.human_status` is kept and updated to the LATEST action
-- for backward compatibility with the CLI loaders.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reviewers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    organization  TEXT,
    role          TEXT,
    expertise     TEXT[] DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         VARCHAR(64) NOT NULL REFERENCES rules(rule_id),
    reviewer_id     UUID NOT NULL REFERENCES reviewers(id),
    action          VARCHAR(16) NOT NULL CHECK (action IN ('approved','rejected','modified','skipped')),
    note            TEXT,
    suggested_text  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_reviews_rule_id     ON rule_reviews(rule_id);
CREATE INDEX IF NOT EXISTS idx_rule_reviews_reviewer_id ON rule_reviews(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_rule_reviews_created_at  ON rule_reviews(created_at);

-- Anon role needs INSERT (signup, post review) and SELECT (load own reviews,
-- count per category).
ALTER TABLE reviewers     DISABLE ROW LEVEL SECURITY;
ALTER TABLE rule_reviews  DISABLE ROW LEVEL SECURITY;

GRANT INSERT, SELECT ON reviewers    TO anon, authenticated;
GRANT INSERT, SELECT ON rule_reviews TO anon, authenticated;
