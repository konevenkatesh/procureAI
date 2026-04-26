# AP Procurement — Knowledge Layer

> India's first procurement rules-as-code asset for the Government of Andhra Pradesh.
> Phase 1 of the BIMSaarthi RTGS hackathon platform.
>
> Scope: build the **knowledge layer** — 400+ verified rules, 750+ clause
> templates, 200+ SHACL shapes, 200+ vector concepts. Application modules
> (Drafter, Validator, Evaluator, Communicator) come later and READ from these stores.

---

## Architecture in one diagram

```
 ┌──────────────────────┐    ┌────────────────────────────┐    ┌─────────────────────────┐
 │ source PDFs (you)    │ →  │ Docling → Markdown sections │ →  │ data/extraction_batches │
 └──────────────────────┘    └────────────────────────────┘    └────────────┬────────────┘
                                                                            │
                                              ┌─────────────────────────────┘
                                              ▼
                              ┌──────────────────────────────────┐
                              │ Claude Code (you, in chat)       │
                              │   reads batch → emits JSON rules │
                              └────────────┬─────────────────────┘
                                           ▼
                              ┌──────────────────────────────────┐
                              │ data/extraction_results/*.json   │
                              └────────────┬─────────────────────┘
                                           ▼
                              ┌──────────────────────────────────┐
                              │ load_extracted_rules.py          │
                              │ → Postgres (status: PENDING)     │
                              └────────────┬─────────────────────┘
                                           ▼
                              ┌──────────────────────────────────┐
                              │ review_cli.py (human approval)   │
                              │ → status: APPROVED               │
                              └────────────┬─────────────────────┘
                                           ▼
              ┌────────────────────────────┼─────────────────────────────┐
              ▼                            ▼                             ▼
     [Clause generation]          [SHACL generation]            [Test-case generation]
     same batch/result loop       same loop (P1 only)            same loop
              ▼                            ▼                             ▼
     ClauseTemplate (Postgres)    SHACL .ttl + Postgres + Fuseki  TestCase (Postgres)
              ▼
     [Telugu translation]
              ▼
     text_telugu populated
```

**Key idea:** every LLM step is done by Claude Code in conversation, not by an
SDK call. The repo ships batch-prep + result-loader scripts. There is no
`ANTHROPIC_API_KEY` anywhere in this codebase.

---

## One-time setup

```bash
# 1. Start services
docker-compose up -d

# 2. Install Python deps
pip install -e ".[dev]"

# 3. Initialise DB (creates tables + loads risk typology seed)
python scripts/setup_db.py

# 4. Read the source-doc checklist and download what you need
cat source_documents/SOURCES.md
# → drop downloaded PDFs/DOCX into the matching raw_pdf/ folders
```

---

## The build pipeline (run in this order)

### Step 1 — Convert raw documents to Markdown
```bash
python scripts/process_all_documents.py
# → source_documents/**/processed_md/*.md
```
Idempotent. Re-run any time you add new source documents.

### Step 2 — Prepare extraction batches
```bash
python scripts/prepare_extraction_batches.py
# → data/extraction_batches/batch_0001.json, batch_0002.json, ...
```

### Step 3 — Extract rules (this is where Claude Code does the work)
Open a Claude Code session and ask:

> Read `data/extraction_batches/batch_0001.json`. Follow the embedded
> `system_prompt`. Write the result to `data/extraction_batches/../extraction_results/batch_0001.json`.

Repeat for each batch. (Or ask: "process batches 1–10 in sequence.")

### Step 4 — Load extraction results into Postgres
```bash
python scripts/load_extracted_rules.py
# → rows in `rules` table with human_status='pending'
```

### Step 5 — Human review
```bash
python builder/review_cli.py review --batch 30
python builder/review_cli.py stats
```
Approve / reject / modify each candidate. Approval rate is the leading indicator
of extraction quality — if it drops below ~60%, refine the extraction prompt
in `builder/rule_extractor.py` and re-run a sample batch.

### Step 6 — Clause-template generation
```bash
python scripts/prepare_clause_batches.py        # build batches from APPROVED rules
# → ask Claude Code to fill data/clause_results/*.json
python scripts/load_clause_results.py
```

### Step 7 — Telugu translation
```bash
python scripts/prepare_telugu_batches.py
# → ask Claude Code to fill data/telugu_results/*.json
python scripts/load_telugu_results.py
```

### Step 8 — SHACL shape generation (P1 rules only)
```bash
python scripts/prepare_shacl_batches.py
# → ask Claude Code to fill data/shacl_results/*.json
python scripts/load_shacl_results.py            # validates Turtle, writes .ttl files
python scripts/load_shacl_to_fuseki.py          # uploads to Jena Fuseki
```

### Step 9 — Test cases (5 per approved rule)
```bash
python scripts/prepare_testcase_batches.py
# → ask Claude Code to fill data/testcase_results/*.json
python scripts/load_testcase_results.py
```

### Step 10 — Vector concepts (P2 rules)
```bash
python scripts/prepare_concept_batches.py
# → ask Claude Code to fill data/concept_results/*.json
python scripts/load_vectors.py                  # BGE-M3 embed + Qdrant upsert
```

### Step 11 — Verify
```bash
python scripts/verify_knowledge_layer.py
# → progress dashboard against production targets
```

---

## How to ask Claude Code to process a batch

The exact prompt that works best:

> Process `data/extraction_batches/batch_0007.json`. Read the file in full,
> follow its `system_prompt` and `instructions_for_operator` exactly, and
> write the JSON result to `data/extraction_results/batch_0007.json`. Validate
> against the schema before writing. Report the count per section.

For bulk:

> Process `batch_0001.json` through `batch_0010.json` from
> `data/extraction_batches/`. For each, write the result file with the same
> name into `data/extraction_results/`. Stop and report if any batch produces
> fewer than 2 rules per section on average — that signals the extractor is
> being too conservative.

---

## Production-ready targets

| Asset            | Target | Where to check |
|------------------|--------|----------------|
| Approved rules   | 400+   | `verify_knowledge_layer.py` → Approved row |
| Clause templates | 750+   | `verify_knowledge_layer.py` → Total templates |
| Clauses w/ Telugu | 750+  | `verify_knowledge_layer.py` → With Telugu |
| SHACL shapes (production-ready) | 200+ | `verify_knowledge_layer.py` |
| Vector concepts in Qdrant | 200+ | `verify_knowledge_layer.py` |
| Risk typologies  | 45     | seeded by `setup_db.py` from `data/risk_typology.json` |

---

## Repo map

```
procureAI/
├── README.md                      ← this file
├── docker-compose.yml             ← Postgres + Qdrant + Fuseki
├── pyproject.toml                 ← Python deps
├── alembic.ini                    ← DB migration config
├── .env                           ← local config (gitignored)
├── .env.example                   ← template
│
├── source_documents/
│   ├── SOURCES.md                 ← download checklist for the operator
│   ├── central/{raw_pdf,processed_md}/
│   ├── ap_state/{raw_pdf,processed_md}/
│   └── sample_tenders/{raw,processed_md}/
│
├── builder/                       ← pipelines (no LLM SDK)
│   ├── config.py                  ← settings via pydantic-settings
│   ├── document_processor.py      ← Docling pipeline
│   ├── section_splitter.py        ← Markdown → sections
│   ├── rule_extractor.py          ← extraction batch prep + loader
│   ├── clause_generator.py        ← clause batch prep + loader
│   ├── telugu_generator.py        ← Telugu batch prep + loader
│   ├── shacl_generator.py         ← SHACL batch prep + loader
│   ├── test_case_generator.py     ← test-case batch prep + loader
│   ├── vector_loader.py           ← VectorConcept batch + BGE-M3 + Qdrant
│   └── review_cli.py              ← Rich/Typer human review
│
├── knowledge_layer/               ← Pydantic schemas + DB models + stores
│   ├── schemas.py                 ← LOCKED contract (Rule, ClauseTemplate, …)
│   ├── models.py                  ← SQLAlchemy ORM
│   ├── database.py                ← engine + session
│   ├── rule_store.py              ← rule CRUD
│   ├── clause_store.py            ← clause CRUD
│   ├── shacl_store.py             ← Postgres + Fuseki helpers
│   └── vector_store.py            ← Qdrant helpers
│
├── ontology/
│   ├── ap_procurement_base.ttl    ← OWL base (Tender / Bid / properties)
│   └── shacl_shapes/              ← generated .ttl files (one per shape)
│
├── scripts/                       ← entry points
│   ├── setup_db.py
│   ├── process_all_documents.py
│   ├── prepare_extraction_batches.py
│   ├── load_extracted_rules.py
│   ├── prepare_clause_batches.py
│   ├── load_clause_results.py
│   ├── prepare_telugu_batches.py
│   ├── load_telugu_results.py
│   ├── prepare_shacl_batches.py
│   ├── load_shacl_results.py
│   ├── load_shacl_to_fuseki.py
│   ├── prepare_testcase_batches.py
│   ├── load_testcase_results.py
│   ├── prepare_concept_batches.py
│   ├── load_vectors.py
│   └── verify_knowledge_layer.py
│
├── data/
│   ├── risk_typology.json         ← 45 seed categories
│   ├── extraction_batches/        ← I/O pairs for each pipeline phase
│   ├── extraction_results/
│   ├── clause_batches/
│   ├── clause_results/
│   ├── telugu_batches/
│   ├── telugu_results/
│   ├── shacl_batches/
│   ├── shacl_results/
│   ├── testcase_batches/
│   └── testcase_results/
│
├── migrations/                    ← Alembic
└── tests/
```

---

## Hard rules — what NOT to do

- ❌ Do not call any external LLM SDK from this codebase. All LLM work is
  performed by Claude Code reading/writing batch files in conversation.
- ❌ Do not modify `knowledge_layer/schemas.py` once data is in Postgres
  without writing a migration.
- ❌ Do not commit `.env`, raw PDFs, or anything in `source_documents/**/raw*/`.
- ❌ Do not skip human review — only `human_status in ('approved', 'modified')`
  rules should ever flow into clause/SHACL/test generation.
- ❌ Do not load SHACL shapes into Fuseki until their test-case pass rate is 100%.
- ❌ Do not load vectors into Qdrant unless `len(embedding) == 1024` (BGE-M3 dim).

---

## Token-budget estimate (for context)

Building the full knowledge layer end-to-end takes ~10.4M Claude tokens spread
across ~100 turns of focused work. See `PROJECT_OVERVIEW.md` for the per-phase
breakdown if you have one. No external API spend.
