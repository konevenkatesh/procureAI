# ProcureAI

> **AI procurement compliance platform for Andhra Pradesh State Government tenders.**
> 4 modules · Knowledge Layer · BOT chat · End-to-end lifecycle coverage.

🌐 **Live demo**: https://procureai.bimsaarthi.com
📄 **Technical proposal**: [`docs/TECHNICAL_PROPOSAL.md`](docs/TECHNICAL_PROPOSAL.md)
🎬 **Demo script**: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
📚 **134 lessons learned**: [`LESSONS_LEARNED.md`](LESSONS_LEARNED.md)

---

## What's live

| Module | URL | Status |
|--------|-----|--------|
| **1. Drafter** | https://procureai.bimsaarthi.com/module1 | ✅ 7-step wizard + workflow_v2 + parallel BoQ generation |
| **2. Pre-RFP Validator** | https://procureai.bimsaarthi.com/module2/validate | ✅ 4-step wizard + 24 Tier-1 validators + file upload |
| **3. Bid Evaluator** | https://procureai.bimsaarthi.com/module3/evaluate | ✅ 5-step wizard + 14 Tier-2 evaluators + SSE live |
| **4. Bidder Communicator** | https://procureai.bimsaarthi.com/module4/conversations | ✅ Chat threads + AI draft + Telugu translate + SMTP (DEGRADED until creds wired) |
| **Knowledge Layer** | https://procureai.bimsaarthi.com/knowledge | ✅ 611 rules + 1577 clauses + 102 templates browsable |
| **BOT chat** | FAB on every page | ✅ RAG over corpus with clickable citations |

---

## At a glance

- **5 Cloud Run services** in `asia-south1` (Mumbai) for DPDP-2023 data residency
- **Supabase Postgres + pgvector** with 3,283 768-dim embeddings (Vertex AI text-embedding-005)
- **Vertex AI Gemini 2.5 Flash + Pro** for content generation
- **Sarvam-M** for English↔Telugu (Module 4 only)
- **134 lessons documented** across 14 development runs (R3 through R14)
- **Cumulative LLM spend**: ~₹63 across all 7 dev runs (R7-R13), well within the ₹100 hackathon cap
- **Hard sentinel preserved**: 18 tracked node_types pinned at `154/351/49/27/3/6/3/611/1577/30/72/8/3/27/27/27/27/12/6` since baseline ingestion

---

## Architecture

```
                          procureai.bimsaarthi.com (Global HTTPS LB)
                                       │
                  ┌────────────────────┴────────────────────┐
                  ▼                                          ▼
       procure-ai-frontend (Cloud Run, Next.js 14)    [browser UI surfaces]
                  │
                  │  Cloud Run ID-token OIDC over runtime SA
                  │
         ┌────────┼────────┬────────────┬───────────┐
         ▼        ▼        ▼            ▼           ▼
       m1-     m2-      m3-          m4-      (BOT chat
     drafter validator evaluator communicator   served from
                                                  frontend)
         │        │        │            │
         └────────┴────────┴────────────┘
                  │
                  ▼
          Supabase Postgres
                  ├─ kg_nodes (18 tracked types, sentinel-pinned)
                  ├─ kg_edges (typed relationships)
                  ├─ demo_evaluation_run (sentinel-safe demo runs)
                  ├─ demo_validation_run (sentinel-safe demo runs)
                  ├─ communication_thread (sentinel-safe threading)
                  ├─ uploaded_draft (sentinel-safe draft uploads)
                  └─ pgvector ANN index (HNSW + cosine)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full architecture document and [`docs/architecture-gcp.md`](docs/architecture-gcp.md) for the GCP-specific deployment topology.

---

## Quickstart for evaluators

### Just want to see the demo

1. Open https://procureai.bimsaarthi.com
2. Click any module in the sidebar
3. Each wizard is self-explanatory; click around

For a guided 10-minute walkthrough, see [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

### Want to read the technical details

Start with [`docs/TECHNICAL_PROPOSAL.md`](docs/TECHNICAL_PROPOSAL.md) — covers architecture, per-module capability, verified-replay-vs-live disclosure, Phase 2 roadmap with migration triggers, cost economics from production runs, hardware projections, DPDP compliance positioning, and prior-art comparison (ALICE / INACIA / ADELE / AIPA).

### Want to dive into the code

```
procureAI/
├── services/                  # 4 Cloud Run backend services
│   ├── _shared/               # FastAPI + Cloud Tasks + Supabase scaffolding
│   ├── m1-drafter/            # Module 1: tender drafting
│   ├── m2-validator/          # Module 2: pre-RFP validation
│   ├── m3-evaluator/          # Module 3: bid evaluation
│   └── m4-communicator/       # Module 4: bidder communications
├── frontend/                  # Next.js 14 App Router
│   ├── app/                   # routes (modules, api/, knowledge, etc.)
│   ├── components/            # shared UI + per-module
│   └── lib/                   # client+server helpers
├── builder/                   # corpus build pipeline (rule/clause extraction)
├── scripts/                   # 80+ Python scripts (ingestion, smokes, validators)
│   ├── run7/                  # R7 corpus build + pgvector
│   ├── run8/                  # R8 3-scale smokes
│   ├── run9/                  # R9 pre-cache + parallel batching
│   ├── run10/                 # R10 rule elaboration
│   ├── tier1_*_check.py       # 24 Tier-1 validators
│   └── bid_*_check.py         # 13 Tier-2 evaluators
├── docs/                      # technical proposal, demo script, architecture
├── LESSONS_LEARNED.md         # 134 lessons across 14 runs
└── README.md                  # this file
```

---

## Key design patterns

These are documented in detail in `LESSONS_LEARNED.md`; here are the headlines:

1. **Hybrid replay+live** (L130) — Modules 2, 3, 4 all use the same pattern: read existing finding/Communication rows from kg_nodes, animate via SSE timing for the demo; new actions write to sentinel-safe regular Postgres tables (`demo_*_run`).
2. **Sentinel discipline** (L123) — Hard sentinel of 18 node-type counts pinned at baseline; every commit verifies; new tables for new actions, never mutations to baseline.
3. **Step-wise wizards with URL state** (L124) — All 4 modules use a single-page wizard with URL-encoded state for shareability + browser-history support.
4. **SSE triplet pattern** (L125) — Every multi-step operation emits 3 event types per step (started / finding / complete) so frontends can render queued/running/complete without polling.
5. **Sarvam-M with PII pseudonymiser** (L127) — PAN/GSTIN/mobile/bidder-name masking before every external API call, restoration after.
6. **DEGRADED mode for every external dep** (L128) — Stale credentials, missing env, slow API all show clear banners; UI stays functional.
7. **pgvector beats premature Qdrant** (L129) — At <50K vectors, pgvector + HNSW + cosine handles ANN search well. Migration triggers documented.
8. **Workflow-level embedding pre-cache** (L118) — ~17 retrieval queries per workflow batched into 1 Vertex API call at startup. Caught the R8.3 capital-scale wall-clock regression.

---

## Repository

🐙 **GitHub**: https://github.com/konevenkatesh/procureAI
✉️ **Maintainer**: konevenkatesh92@gmail.com (BIMSaarthi Technologies)
📋 **License**: see [`LICENSE`](LICENSE) (or this section will be filled at submission time)

---

## Development history

| Run | Scope | Status |
|----:|-------|--------|
| R3-R6 | Backend services + Cloud Run deploy + first cloud production | Done |
| R7 | M1 corpus build (1095 embeddings) + 9 SBD sections + 993 BoQItemSpec + 72 TechSpecTemplate | Done |
| R8 | M1 3-scale smokes — Banaganapalli + LPS + HOD Towers | Done (R8.3 partial; fixed in R9) |
| R9 | M1 fixes — pre-cache + concurrency tune + capital scale ✓ | Done |
| R10 | Knowledge Layer + BOT chat + 611-rule AI elaboration | Done |
| R11 | Module 3 step-wise evaluation | Done |
| R12 | Module 4 chat threads + Telugu + SMTP DEGRADED | Done |
| R13 | Module 2 Pre-RFP validator (replay + live) | Done |
| R14 | TECHNICAL_PROPOSAL.md + demo script + README polish | Done (this commit) |

134 lessons captured in [`LESSONS_LEARNED.md`](LESSONS_LEARNED.md).

---

*Last updated: 2026-05-13 (R14 wrap)*
