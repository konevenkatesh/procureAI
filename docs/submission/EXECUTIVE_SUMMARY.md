# ProcureAI — Executive Summary (1 page)

**Hackathon tracks:** RTGS (Real-Time Governance Systems) · I&I (Innovation & Implementation)
**Submitter:** BIMSaarthi Technologies Pvt Ltd · Hyderabad
**Live demo:** https://procureai.bimsaarthi.com
**Region:** Google Cloud `asia-south1` (Mumbai) — DPDP §16(1) compliant

---

## The platform in one sentence

An audit-defensible AI platform that spans the entire AP procurement lifecycle — drafts tender documents from the rule library, validates draft RFPs against AP-State + Central + CVC layers, evaluates bidder submissions through 14 typology-checks plus 4 aggregators, and communicates with bidders bilingually in English + Telugu via Sarvam-M with DPDP pseudonymisation.

## Four modules, four roles

| # | Module | Role | Current state |
|---|---|---|---|
| 1 | **Drafter** | Compose a draft RFP from the 1,223-rule library | Phase-1 demo: 3 sample drafts; LangGraph 3-gate workflow in Phase 2 |
| 2 | **Pre-RFP Validator** | Audit a draft against 24 Tier-1 typologies via BGE-M3 retrieval + LLM grading | 24 typologies live; 154 ValidationFindings on the 6-doc corpus; cloud Qdrant migration in Phase 2 |
| 3 | **Post-RFP Evaluator** | Evaluate bidder submissions through 14 Tier-2 validators + 4 aggregators | 351 BidEvaluationFindings · 27 EligibilityMatrix · 3 TenderRanking (B9 effective L1 across 3 tenders) · 6 BidAnomalyFinding (ALB + cartel) · 3 ComparativeStatement |
| 4 | **Communicator** | Bidder-facing + internal communications in EN + TE | 75 Communications across 10 types · NEW bilingual Bidder Clarification Q&A flow live with Sarvam-M |

## Why this matters for AP

1. **Audit-defensible by design.** Every action lands as a kg_node with `audit_id`, citation chain to specific GFR / AP-GO / CVC clauses, and source-text verification (no LLM hallucination paths). Cloud Audit Logs (DATA_READ + DATA_WRITE) cover Cloud Run + Cloud Storage + Secret Manager with 400-day retention to GCS.
2. **DPDP-resident.** Every Cloud Run service, every artifact bucket, every audit-log archive lives in `asia-south1`. PII (PAN, GSTIN, mobile, bidder names) is pseudonymised before any external API call (Sarvam-M, OpenRouter, Vertex AI).
3. **Bilingual at the boundary.** Bidder-facing communications generate side-by-side English + Telugu via Sarvam-M `/translate`. Bidders can submit clarifications in Telugu; officers respond in English; both sides see both languages.
4. **Named-risk explainability.** Aggregators carry an explicit verdict precedence (HARD_BLOCK > WARNING > GAP > QUALIFIED). Each verdict drills down to the underlying finding `node_id` and to the cited rule (e.g., `AP-GO-175 PBG 2.5%`).

## Five differentiators vs Brazil's ALICE (CGU) and INACIA (TCU)

1. **AP-state coverage from day 1** — 80 AP-GO rules pre-loaded with explicit defeats-from-Central markup (e.g., MPW-002, AP-GO-229 defeats 38 Central rules); ALICE and INACIA only ship Central rules and let states add their own.
2. **End-to-end lifecycle in one platform** — drafting, validation, evaluation, communication. ALICE is evaluation-only; INACIA is audit-only. We span all four.
3. **Bilingual production output** — Telugu rendering via Sarvam-M is the path to actual citizen-facing artefacts (regret letters, award notifications). ALICE/INACIA ship Portuguese only.
4. **DPDP-resident managed-services stack** — Cloud Run + GCS + Secret Manager in asia-south1 with audit-log archival to a 400-day GCS sink. No self-hosting required for demo; on-premise path documented for production.
5. **Three-state contract** — Tier-1 typologies return COMPLIANT / UNVERIFIED / ABSENCE (L35), and Tier-2 evaluators return QUALIFIED / FLAGGED / GAP_INSUFFICIENT_DATA / SKIP_NOT_APPLICABLE (L61). This kills the "false-confidence" failure mode that plagues binary classifiers in procurement audit.

## Quantified PoC bar

| Metric | Target | Current |
|---|---|---|
| Tier-1 typology coverage | 25 typologies | **24 typologies live** (1 deferred) |
| Tier-2 evaluator coverage | 14 evaluators | **14 evaluators with seed data** |
| Aggregator coverage | 4 (EligMatrix, TenderRank, AnomalyDetector, ComparativeStmt) | **4 of 4 live** |
| Communication types | 10 types | **10 types · 75 nodes · all bidder-facing bilingual** |
| Compliance latency | < 5 s per typology | ✓ (current corpus) |
| End-to-end response on the LB | < 3 s (cold start excluded) | ✓ (smoke: 1.3 s avg) |
| Knowledge graph rule count | 1,200+ rules | **1,223 rules · 700 clause_templates · 200+ SHACL shapes** |
| TLS cert | Google-managed, auto-renewing | ✓ ACTIVE |
| Audit log retention | 400 days | ✓ |

## Production roadmap (12-week post-hackathon)

1. **Weeks 1-2:** Module 1 LangGraph drafter (state checkpointer on Supabase); on-premise Qdrant on Mumbai-resident GKE for Module 2.
2. **Weeks 3-6:** apeprocurement.gov.in API integration (versioned client + retry posture).
3. **Weeks 7-10:** RBAC + Keycloak SSO on Cloud Run; VPC Service Controls on a Premium-tier billing account.
4. **Weeks 11-12:** Pilot deployment on a single AP department's procurement workflow with the actual procurement officer in the loop.

## Cost posture

- **Live demo:** ~₹1,920/month idle (Cloud Run scale-to-zero + LB baseline). Demo activity adds ~₹100/month.
- **Production (5,000 tenders/year):** ~₹15,000/month on managed services, or ~₹8,000/month on a 4-node on-premise Kubernetes cluster.
- **Marginal cost per tender end-to-end** (Draft → Validate → Evaluate → Communicate): ₹15–20.

---

**Architecture deep-dive:** `docs/architecture-gcp.md`
**Methodology catches:** `LESSONS_LEARNED.md` L94–L100
**Demo script:** `docs/submission/DEMO_SCRIPT.md`
