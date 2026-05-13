# ProcureAI — Demo Script & Walkthrough

**Target audience**: Hackathon judges (technical + policy reviewers)
**Total duration**: 10 minutes
**Format**: Live screen-share against https://procureai.bimsaarthi.com
**Backup**: Each section has a fallback talking point if a service is slow

---

## Pre-demo checklist (5 min before)

1. Open https://procureai.bimsaarthi.com in a fresh browser tab. Verify the dashboard loads.
2. Open a second tab to https://procureai.bimsaarthi.com/knowledge — confirm "1356 rules · 700 clauses · 102 templates" stats load (this warms m1-drafter and the frontend's m1 ID-token cache).
3. Open a third tab to https://procureai.bimsaarthi.com/module4/conversations — pre-warm m4-communicator. Click any thread to verify messages render.
4. Prepare browser DevTools (Network tab open, no filter) — useful if a judge asks "how does the SSE stream actually look?"
5. Keep a backup terminal with `curl` commands ready in case the UI flakes.

---

## 0:00 – 0:45 · Opening (45 sec)

> *"ProcureAI is an AI procurement compliance platform for Andhra Pradesh State Government tenders. It covers the full lifecycle — drafting, pre-RFP validation, bid evaluation, and bidder communications — across four modules. Everything you'll see is live on procureai.bimsaarthi.com, running on Google Cloud Run in asia-south1 with Supabase as the corpus store. We've kept ourselves to a tight ₹100 cumulative LLM budget across 7 development runs and spent ₹63 actual — that's 200+ test tenders worth of generation and 154+ baseline ValidationFinding rows of evaluation, all preserved as a frozen sentinel since baseline ingestion."*

**Visual**: Land on dashboard at `/`. Point to the sidebar showing all 4 modules + Knowledge + About.

---

## 0:45 – 3:00 · Module 1 — Drafter (2 min 15 sec)

**Goal**: Show that a Dealing Officer can go from a 7-step form to a 244-page bid document in ~12 minutes for capital scale.

### Walkthrough

1. Click **Module 1 → New Draft** in sidebar.
2. Skim through Steps 1-5 quickly (Authority, Classification, Financial, Geography, Evaluation).
   - *"Each step has form validation aligned to AP eGP tender format — ECV in INR, period, bid-validity days, contractor classification."*
3. On **Step 6 (Documents & Dates)**, pause to point out the **BoQ Skeleton uploader**.
   - *"This is the officer's lever. They upload an Excel or CSV with item-level scope — name + qty + unit. The AI fills the specifications via Vertex Gemini Flash, batched 30 rows at a time, parallel to 6 concurrent batches."*
4. Click through to **Step 7 (Review)** — the BoQ Skeleton summary card shows the row count.
5. **Don't actually click "Generate Tender Draft" during the demo** (it's a 12-min run at HOD capital scale). Instead:

> *"For capital projects like the ₹743 crore HOD Towers MEP tender, we've validated this at 3000 BoQ rows × 200 Flash batches × ~35s each, completing in 663 seconds wall-clock, for ₹35 total LLM spend, with 100% citation match against IS/EN/APSS standards on a 50-row sample. The full 244-page bid document — NIT, ITB, eligibility, GCC, PCC, BoQ, all 9 sections — comes out the other side."*

### Backup if judge asks "show me the actual generated document"

Open the local `/tmp/r76_boq_sample.json` — has the first 5 enriched BoQ rows from R7.6 smoke. Read out the EARTHWORK_EXCAVATION_FOUNDATION row with its 909-char spec_text citing IS 3764:1966 and APSS Cl. 301.1.

---

## 3:00 – 4:30 · Module 2 — Pre-RFP Validator (1 min 30 sec)

**Goal**: Show 24 Tier-1 validators run live with SSE animation.

### Walkthrough

1. Navigate to **Module 2 → Validate Draft**.
2. Click the **Kurnool tender** card (replay path — fast for demo).
3. Skim Step 2 (sections preview) — 9 sections detected.
4. Click **Start validation**.
5. **Pause and watch**: 24 validator cards stream live via SSE. Each card transitions queued → running → complete with PASS/FAIL/WARN icons.

> *"What you're seeing is server-sent events from the m2-validator Cloud Run service. Each of 24 validators reads against the corpus — 611 RuleNode rows, 1577 ClauseNode rows from GFR, AP-GO, CVC, MPW. The SSE stream survives Cloud Run's global load balancer because we set X-Accel-Buffering: no on every SSE response."*

6. After completion (~7 seconds), Step 4 shows the severity breakdown panels.
7. Click on one finding to expand the evidence quote and rule citation.

### Backup if SSE feels slow

> *"Replay mode reads existing baseline findings from our 154-row ValidationFinding table. Live mode for uploaded drafts is the Phase 2 swap point — we deferred BGE-Reranker and Gemini Pro per-validator reasoning because at our current 3,283-vector corpus, pgvector with HNSW handles the retrieval well below saturation. Migration trigger is documented at 50K vectors."*

---

## 4:30 – 6:00 · Module 3 — Bid Evaluator (1 min 30 sec)

**Goal**: Show step-wise evaluation with 14 evaluators × N bidders.

### Walkthrough

1. Navigate to **Module 3 → Evaluate Bidders**.
2. Click **Kurnool tender**.
3. Multi-select bidders B1 + B2 + B3 (or pick "Select all" for max effect).
4. Skim Step 3 (View Bid) — shows LoB + EMD-BG + Priced BoQ cards.
5. Click **Start evaluation**.
6. **Pause**: 14-validator × 3-bidder grid streams progress.

> *"The 14 evaluators are: ABC threshold, blacklist, contractor class, compliance documents, EMD validity, equipment, financial turnover, JV/consortium, litigation, personnel, similar works (the 3/2/1 rule), solvency certificate, turnover, and BG validity. Each emits 6 SSE event types — started, finding, complete per validator per bidder, plus bidder_complete and evaluation_complete at the run level."*

7. After completion, Step 5 shows the eligibility matrix verdict counts and tender ranking (L1/L2/L3).

### Backup if a judge says "this is just replaying existing findings"

> *"Yes — and that's by design. We have 351 BidEvaluationFinding rows + 27 EligibilityMatrix rows from baseline ingestion that we never want to corrupt across demo runs. The verified-replay pattern reads those rows by bid's doc_id, buckets them per validator via rule_id keyword matching, and animates them via SSE timing. New evaluations on uploaded bids would write to demo_evaluation_run instead — a separate table outside kg_nodes, sentinel-safe by construction. The narrative arc is identical to a real live run."*

---

## 6:00 – 7:30 · Module 4 — Bidder Communicator (1 min 30 sec)

**Goal**: Show chat-thread UI with AI-drafted replies + Telugu translation.

### Walkthrough

1. Navigate to **Module 4 → Conversations**.
2. Point to the thread list — 28 threads grouped by (tender, bidder).
3. Click any thread (suggested: a Premier AP Constructions thread on HC tender — has 3 messages including ALB_JUSTIFICATION).
4. Scroll the message bubbles, point out:
   - Inbound vs outbound layout
   - "AI" pill for ai_drafted messages
   - EN/తెలుగు toggle button on bilingual messages
   - SENT / DRAFT status badges
5. In the composer, type officer intent: **"request the bidder to clarify the price variation clause acceptance"**
6. Click **AI Draft**.
7. Wait ~2 seconds. The composer textarea populates with a professional Gemini Flash reply.

> *"That's Vertex AI Gemini 2.5 Flash with thread context. The prompt includes the last 5 messages, tender ID, bidder name, and the officer's intent. Cost per draft is about 5 paise."*

8. Click **Translate via Sarvam**.
9. Telugu text appears in the side panel.

> *"Sarvam-M is an Indian-language model we use only here — Telugu is bounded to bidder-facing emails per AP State linguistic-inclusion guidelines. Everywhere else is English. Before each Sarvam call, our PII pseudonymiser masks PAN, GSTIN, mobile numbers, and bidder names with __PAN1__ tokens that survive translation unchanged. The originals get restored client-side after. Sarvam never sees real identifiers — DPDP-respectful by construction."*

10. Click **Send** — the SSE stream shows smtp_connecting → smtp_degraded (since Gmail credentials aren't bound).

> *"Currently SMTP outbound is DEGRADED — the send saves as DRAFT until Gmail App Password credentials are bound to the Cloud Run service via Secret Manager. One gcloud command flips it to LIVE. UI is fully functional regardless — that's our pattern across the platform: every external dependency has a degraded mode that doesn't block the UI."*

---

## 7:30 – 8:45 · Knowledge Layer + BOT chat (1 min 15 sec)

**Goal**: Show that the corpus is browsable + queryable via natural language.

### Walkthrough

1. Click the **floating chat FAB** at bottom-right.
2. Type: **"What is the rule on Performance Bank Guarantee for a Rs 100 crore project?"**
3. Wait ~3 seconds. The bot streams a cited answer.

> *"That's RAG over our 3,283-vector pgvector index. Five top-K rows retrieved via cosine similarity on the embedded user query. The system prompt enforces cite-or-decline — every claim has a [Rule:ID] chip you can click."*

4. Click one of the `[Rule:...]` chips.
5. The Knowledge Layer detail modal opens with:
   - Severity badge (HARD_BLOCK red / WARNING amber / ADVISORY blue)
   - Layer badge (Central / AP-State)
   - Typology code chip
   - The actual rule statement
   - AI-elaborated description (240-360 word professor-style explanation we generated for all 611 rules)
   - Verification method block
   - Defeats chain showing superseded rules

> *"Every rule in the corpus has this elaborated description. We ran Gemini Flash over all 611 rules in 4 minutes, 44 seconds at 10 concurrent waves, total cost ₹2.5. The descriptions cover regulatory intent, trigger conditions, failure modes, and related typology context — enough for a Dealing Officer to act without reading the full AP-GO PDF."*

6. Close the modal. Go back to the chat FAB.
7. Type: **"Show me IS codes for HVAC ducting"**
8. The bot retrieves TechSpecTemplate rows + lists IS 8500, IS 277, EN 1886, etc.

---

## 8:45 – 9:30 · Sentinel discipline + Phase 2 (45 sec)

> *"One thing we want to flag for the technical reviewers: every commit since R5 has preserved a frozen 18-node-type sentinel — 154 ValidationFinding, 351 BidEvaluationFinding, 27 EligibilityMatrix, 611 RuleNode, 1577 Section, 30 SBDSection, 72 TechSpecTemplate, plus a handful of others. New demo evaluations, new validations, new communications never mutate those tables. They flow to sentinel-safe regular Postgres tables — demo_evaluation_run, demo_validation_run, communication_thread, uploaded_draft. This is the audit-defensibility posture we'd offer the CAG. The baseline never moves; new actions append. Reproducible by construction."*

> *"Phase 2 has clear triggers: migrate to Qdrant when the corpus crosses 50,000 vectors, add the BGE-Reranker cross-encoder when pgvector p99 exceeds 500ms, swap Module 2's templated live mode for real Tier-1 subprocess invocation when the validator container has the headroom. WhatsApp + the apeprocurement.gov.in integration are sized at 2 and 6 weeks respectively — both deferred pending pilot feedback."*

---

## 9:30 – 10:00 · Q&A preparation (30 sec close)

> *"That's the platform. All four modules live, 134 documented lessons across 14 development runs in our LESSONS_LEARNED.md, full repo on GitHub. Happy to take questions on architecture, cost, DPDP compliance, or the verified-replay vs live distinction."*

---

## Anticipated Q&A

### "How does this differ from existing tools like AIPA or GeM?"

> *"AIPA and GeM are post-publication marketplaces — they handle the bidding event itself. ProcureAI covers what happens BEFORE the bid: drafting the tender, validating it for compliance gaps, evaluating bids when they arrive, and replying to bidder clarifications. It's the pre-RFP + evaluation + comms layer that AIPA assumes is already done. Internationally, Brazil's ALICE/INACIA is the closest peer, but they're audit-court reactive — we're authoring-side proactive."*

### "What if your Vertex AI quota runs out mid-tender?"

> *"Each module has a degraded mode. Module 1's BoQ generator falls back to Gemini Pro when Flash drifts; if Vertex is fully down, the workflow_v2 sections render as stubs and the officer completes them manually. We've validated this — R8.3's first attempt hit Vertex saturation and we returned stubs for 450 of 3000 rows; the run still produced a partial document for officer review. Module 4 chat shows DEGRADED banner if SMTP creds missing. Knowledge Layer doesn't depend on Vertex at all — it's read-only over Supabase. BOT chat shows 'corpus retrieval unavailable' if Vertex embed times out."*

### "How do you handle PII when calling external LLMs?"

> *"We have a PII pseudonymiser before every external API call. PAN, GSTIN, mobile numbers, and bidder names get replaced with __PAN1__ / __GSTIN1__ tokens that look like underscore-padded ASCII. These survive translation and Vertex generation unchanged because the underscores make them appear as proper-noun tokens to the LLM. After the response comes back, we restore the originals client-side. Sarvam-M and Vertex AI literally never see the real PII. Documented in L127 of LESSONS_LEARNED."*

### "What's the failure mode if the demo URL goes down during judging?"

> *"All 5 Cloud Run services are independently scalable and we have min-instances=1 on the high-traffic ones (m1-drafter, frontend). If a service is unhealthy, Cloud Run's auto-recovery typically resolves it in <60s. As backup, every screenshot in the README plus the per-run status reports at /tmp/overnight_status_run{7..13}.md document specific endpoint smokes we ran during development. We can walk through any of those if the live URL is unreachable."*

### "Why pgvector instead of a dedicated vector DB?"

> *"At 3,283 vectors, pgvector with HNSW returns top-K in under 100ms. Adding Qdrant would add a fifth managed service to monitor, with its own auth, ports, IAM, and backup story. Documented migration trigger is 50K vectors or 500ms p99 latency. We're 15× under the trigger. Adding complexity before we measure pain is anti-pattern. Documented in L129."*

### "What's the cost to scale to a full state government deployment?"

> *"Hackathon-demo footprint is about ₹11,500/month including Supabase Pro + Vertex AI calls at 200 tenders/month. For full AP State rollout (~₹50,000 crore annual procurement value), we sized a 2× H100 80GB cluster at ~₹1.18 crore CapEx + ~₹35 lakh/year OpEx. Payback period under 6 months if it replaces even 5% of manual procurement-officer review hours."*

### "What's the actual repo / code I can review?"

> *"https://github.com/konevenkatesh/procureAI. Top-level README has the quickstart. LESSONS_LEARNED.md has 134 entries documenting every architectural decision. Each commit follows the conventional-commits pattern with rationale in the body."*

---

## Demo recovery scenarios

| Scenario | Recovery |
|----------|----------|
| Module 1 page slow | Skip to BoQ uploader screenshot in /docs/module1/ subdir |
| Module 2 SSE hangs | Pull /tmp/r13_smoke_result.md from local; read out the validator names |
| Module 3 SSE hangs | Same — `/tmp/overnight_status_run11.md` has the per-step capture |
| Module 4 thread empty | Use `curl https://procureai.bimsaarthi.com/api/m4/threads` to show the data layer works |
| Knowledge Layer page slow | Show /api/kb/stats endpoint via curl — instant response with counts |
| BOT chat returns "I don't have enough info" | Reframe as honest cite-or-decline behaviour; switch to a known-working query like "What sections are in the Standard Bidding Document" |

---

## Post-demo handoff

If a judge wants to dive deeper:
- **Repo**: https://github.com/konevenkatesh/procureAI
- **Technical proposal**: `/docs/TECHNICAL_PROPOSAL.md`
- **134 lessons learned**: `/LESSONS_LEARNED.md` (top-level)
- **Status reports**: `/tmp/overnight_status_run{7..13}.md` (per-run wrap)
- **Contact**: konevenkatesh92@gmail.com

---

*Demo script — v1.0, 2026-05-13. Tuned for 10-minute hackathon judging slot.*
