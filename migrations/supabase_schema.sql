-- Auto-generated DDL from knowledge_layer/models.py
-- Paste into Supabase SQL Editor (Project → SQL Editor → New query)

CREATE TABLE clause_templates (
	clause_id VARCHAR(64) NOT NULL, 
	title VARCHAR(256) NOT NULL, 
	text_english TEXT NOT NULL, 
	text_telugu TEXT, 
	parameters JSON, 
	applicable_tender_types JSON, 
	mandatory BOOLEAN, 
	position_section VARCHAR(128), 
	position_order INTEGER, 
	cross_references JSON, 
	rule_ids JSON, 
	valid_from VARCHAR(16) NOT NULL, 
	valid_until VARCHAR(16), 
	human_verified BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (clause_id)
);

CREATE TABLE risk_typology (
	code VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	definition TEXT NOT NULL, 
	rule_ids JSON, 
	severity VARCHAR(16) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	alice_equivalent VARCHAR(128), 
	PRIMARY KEY (code)
);

CREATE TABLE rules (
	rule_id VARCHAR(64) NOT NULL, 
	source_doc VARCHAR(128) NOT NULL, 
	source_chapter VARCHAR(128), 
	source_clause VARCHAR(128), 
	source_url VARCHAR(512), 
	layer VARCHAR(32) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	pattern_type VARCHAR(2) NOT NULL, 
	natural_language TEXT NOT NULL, 
	verification_method TEXT NOT NULL, 
	condition_when TEXT NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	typology_code VARCHAR(64) NOT NULL, 
	generates_clause BOOLEAN, 
	defeats JSON, 
	defeated_by JSON, 
	shacl_shape_id VARCHAR(64), 
	vector_concept_id VARCHAR(64), 
	valid_from VARCHAR(16) NOT NULL, 
	valid_until VARCHAR(16), 
	extracted_from VARCHAR(256), 
	extraction_confidence FLOAT, 
	critic_verified BOOLEAN, 
	critic_note TEXT, 
	human_status VARCHAR(16), 
	human_note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (rule_id)
);

CREATE TABLE vector_concepts (
	concept_id VARCHAR(64) NOT NULL, 
	rule_ids JSON, 
	canonical_name VARCHAR(256) NOT NULL, 
	aliases JSON, 
	sac_summary TEXT NOT NULL, 
	threshold_trigger JSON, 
	applicable_tender_types JSON, 
	similarity_threshold FLOAT, 
	severity VARCHAR(16) NOT NULL, 
	qdrant_point_id VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (concept_id)
);

CREATE TABLE shacl_shapes (
	shape_id VARCHAR(64) NOT NULL, 
	rule_id VARCHAR(64) NOT NULL, 
	turtle_content TEXT NOT NULL, 
	test_cases_pass INTEGER, 
	test_cases_fail INTEGER, 
	production_ready BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (shape_id), 
	FOREIGN KEY(rule_id) REFERENCES rules (rule_id)
);

CREATE TABLE test_cases (
	test_id VARCHAR(64) NOT NULL, 
	rule_id VARCHAR(64) NOT NULL, 
	document_excerpt TEXT NOT NULL, 
	expected_result VARCHAR(8) NOT NULL, 
	expected_severity VARCHAR(16), 
	reasoning TEXT NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (test_id), 
	FOREIGN KEY(rule_id) REFERENCES rules (rule_id)
);


-- ─────────────────────────────────────────────────────────────────────────────
-- Supabase-specific: disable Row Level Security so the anon key can
-- read AND update the rules table from the portal browser session.
-- For production, enable RLS and add policies tied to authenticated users.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE rules            DISABLE ROW LEVEL SECURITY;
ALTER TABLE clause_templates DISABLE ROW LEVEL SECURITY;
ALTER TABLE risk_typology    DISABLE ROW LEVEL SECURITY;
ALTER TABLE shacl_shapes     DISABLE ROW LEVEL SECURITY;
ALTER TABLE test_cases       DISABLE ROW LEVEL SECURITY;
ALTER TABLE vector_concepts  DISABLE ROW LEVEL SECURITY;

-- Grant explicit access to the anon role (Supabase REST uses this role
-- by default for unauthenticated requests with the anon API key).
GRANT SELECT, UPDATE ON rules            TO anon, authenticated;
GRANT SELECT          ON clause_templates TO anon, authenticated;
GRANT SELECT          ON risk_typology    TO anon, authenticated;
GRANT SELECT          ON shacl_shapes     TO anon, authenticated;
GRANT SELECT          ON test_cases       TO anon, authenticated;
GRANT SELECT          ON vector_concepts  TO anon, authenticated;
