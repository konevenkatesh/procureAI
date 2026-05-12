# Risk Register

10 named risks · each with a current mitigation and a residual-risk rating after that mitigation is applied. Reviewed 2026-05-12.

| # | Risk | Current mitigation | Residual rating |
|---|---|---|---|
| 1 | **LLM hallucination in tender drafts or verdicts** | Constrained structured output: each validator emits JSON with a fixed schema, validated client-side; evidence quotes are verified against source text with `difflib.SequenceMatcher` threshold ≥85 before persisting (L24 evidence guard). Validators with score < 85 emit `UNVERIFIED` instead of a false positive. | **LOW** |
| 2 | **DPDP non-compliance — PII leakage to external LLM/translation APIs** | `services/m4-communicator/app/main.py` pseudonymises PAN, GSTIN, mobile numbers, and bidder names with regex + caller-supplied identifier list before Sarvam-M call; restores original strings client-side after translation. Sarvam-M never sees real PII. Cloud Audit Logs DATA_READ + DATA_WRITE on Cloud Run + GCS + Secret Manager, 400-day retention. | **LOW** |
| 3 | **Vendor lock-in (LangGraph, OpenRouter, Sarvam-M, GCP)** | Code is provider-agnostic: OpenRouter is an OpenAI-compatible API, swappable to any model serving endpoint. Sarvam-M-24B has a self-hostable inference model. LangGraph workflows are state-machine code — the engine can be swapped. GCP Cloud Run is Knative-compatible; can run on any k8s. | **MEDIUM** |
| 4 | **Hardware unavailability of Indic LLM at production scale** | Cascade tier: Sarvam-M `mayura:v1` → cached fall-through → fallback to indictrans2-1B for translation; mistral-small-3.2 → qwen-2.5-72b → fallback to a smaller open-source model for grading. Filesystem cache in `scripts/m4_drafters/_sarvam_client.py` covers ~70% of repeat translations after warmup. | **MEDIUM** |
| 5 | **Adversarial bidder inputs (prompt injection in clarification text)** | Input validation: clarifications are persisted as data, never used as system-prompt context for other validators. Bidders cannot trigger arbitrary validator runs (only `/submit_clarification` is bidder-facing; everything else is officer-facing). Cloud Run `--no-allow-unauthenticated` on backends + ID-token gate prevents direct bidder access to the validators. | **LOW** |
| 6 | **Sarvam-M API downtime mid-bidding-window** | Two-leg fallback: (a) filesystem cache (`/tmp/sarvam_cache/`) covers repeat translations after first call; (b) on-prem deployment ships Sarvam-M-24B local inference, removing the external dependency entirely. Frontend gracefully degrades: bidder sees "Translation queued; English version available now" with retry. | **MEDIUM** |
| 7 | **Qdrant cloud-cost spike if rule library grows** | Qdrant Cloud bills by stored-vector count and tier. Mitigation: shift Qdrant to self-hosted on GKE asia-south1 (~₹3,000/month for a 4 GB cluster, fixed cost) once the Phase-2 migration lands. Vector store size is bounded by clause_template count (currently 700 + future ceiling 1,200 — fits in 4 GB BGE-M3 embeddings). | **LOW** |
| 8 | **`apeprocurement.gov.in` API breaking changes** | Versioned integration layer (planned for Phase 2): each call goes through `modules/apep_client/{vN}_*.py` with a single `from_apep_to_kg_node()` mapper. Old/new endpoints can coexist for the transition. Contract tests assert payload shape per version. | **MEDIUM** |
| 9 | **CAG audit failure — verdict can't be traced** | Every kg_node carries `audit_id` (12-char SHA256 hash), `source_ref` (script path + version), `created_at` (UTC ISO), and a citation chain to specific GFR / AP-GO / CVC rules. Hard sentinels (154/351/27/3/6/3/75) are pinned in tests and CI fails if they drift. Cloud Audit Logs archived to GCS for 400 days covers the post-award challenge window. | **LOW** |
| 10 | **Production migration overrun** | Phase 2 plan has 4 explicit milestones with go/no-go gates: (a) Module 1 LangGraph drafter with Supabase checkpointer; (b) Qdrant on GKE asia-south1; (c) apeprocurement.gov.in client v1; (d) pilot with a single AP department. Each milestone is 2-3 weeks. Total Phase 2: 12 weeks. Failure of any milestone = rollback to the demo platform, not a full halt. | **MEDIUM** |

---

## Cross-cutting controls

- **Sentinel preservation as a CI gate** — every commit run-tests `154 / 351 / 27 / 3 / 6 / 3 / 75` (Communication is allowed to grow but not shrink); failure aborts the merge.
- **Application-level egress allowlist** — services have no shell, no arbitrary URL fetcher, no user-supplied URL passed through. The 5 hosts reachable (Supabase REST · Sarvam-M · OpenRouter · Vertex AI · metadata server) are hard-coded and auditable from source.
- **Secret-handling discipline** — secrets piped via stdin to `gcloud secrets versions add --data-file=-`, never `echo -n`, never in CLI args, never in commit messages. Per `LESSONS_LEARNED.md L94`.
- **Read-only worker default** — m3 and m4 `/worker` endpoints are verified-read for the existing aggregator/communication outputs. Re-execution of underlying scripts is deferred to a follow-up commit so a mid-demo bug can't drift hard sentinels.

---

## What we explicitly do NOT mitigate (acceptable residual risk)

- **VPC Service Controls** are not in place because the current billing tier doesn't grant the org-level `accesscontextmanager.policyAdmin` role. The two-leg fallback (application-level allowlist + audit logs) is the substitute. Production-readiness requires Premium-tier billing.
- **Customer-managed encryption keys (CMEK)** are not configured on GCS / Secret Manager. Google-managed encryption applies at rest. CMEK is the next compliance lever for DPDP-strict deployments.
- **Real bidder uploads** are not yet accepted. Today's bid data is synthetic (9 bidders × 3 tenders). Adding bidder document upload triggers a separate review for file-malware scanning, virus signatures, and document parsing safety.
- **Cloud DNS** is not used; DNS lives at GoDaddy. Cert provisioning + record propagation work as expected, but a registrar outage on GoDaddy would take the custom domain offline. Mitigation: keep `*.run.app` URL live as the always-on fallback.

---

**Last reviewed:** 2026-05-12 · BIMSaarthi Technologies
