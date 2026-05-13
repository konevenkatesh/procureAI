-- R10.3 — pgvector RPC for BOT chat retrieval.
-- Returns top-K kg_nodes across RuleNode + Section + TechSpecTemplate + SBDSection
-- ordered by cosine distance to a query embedding.
--
-- Deploy via Supabase MCP apply_migration or psql:
--   psql $SUPABASE_URL < services/m1-drafter/sql/r10_chat_retrieve.sql

CREATE OR REPLACE FUNCTION kb_chat_retrieve(
    query_embedding vector(768),
    top_k int DEFAULT 5
) RETURNS TABLE (
    node_id   uuid,
    node_type text,
    label     text,
    snippet   text,
    distance  float
) LANGUAGE sql STABLE AS $$
    SELECT
        n.node_id,
        n.node_type,
        n.label,
        COALESCE(
            n.properties->>'content_md',
            n.properties->>'spec_text',
            n.properties->>'rule_text',
            n.properties->>'description',
            n.label
        )::text AS snippet,
        (n.embedding <=> query_embedding)::float AS distance
    FROM kg_nodes n
    WHERE n.node_type IN ('RuleNode', 'Section', 'TechSpecTemplate', 'SBDSection')
      AND n.embedding IS NOT NULL
    ORDER BY n.embedding <=> query_embedding ASC
    LIMIT top_k;
$$;

-- Grant execute to authenticated + service role so PostgREST can RPC it.
GRANT EXECUTE ON FUNCTION kb_chat_retrieve(vector, int) TO authenticated, service_role, anon;
