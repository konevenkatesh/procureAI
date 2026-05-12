# Demo Script — 3-minute walkthrough

**Total length:** 3 minutes (180 seconds), broken into 6 segments.
**URL on screen:** https://procureai.bimsaarthi.com
**Speaker:** Venkatesh Kone, Founder, BIMSaarthi Technologies

---

## 0:00 — 0:15 · Opening (15s)

> "I'm Venkatesh, founder of BIMSaarthi Technologies. This is **ProcureAI** — an end-to-end AI platform for AP government procurement. It's live now in Google Cloud Mumbai (`asia-south1`), DPDP-compliant, with a full audit trail to a 400-day Cloud Storage archive. Four modules, four phases of the lifecycle. Let me show you all four in three minutes."

**On screen:** open https://procureai.bimsaarthi.com → dashboard renders. Point at the green "asia-south1 · DPDP-compliant" badge in the header.

---

## 0:15 — 1:00 · Demo 1: Dashboard + sentinel stats (45s)

> "The dashboard surfaces four numbers that bound the demo:
>   - **12** bidder profiles in our synthetic corpus
>   - **154** validation findings — that's Tier-1 rule violations across our 6-document tender library
>   - **351** bid evaluation findings — Tier-2 checks on 9 bidders across 3 tenders
>   - **75** communications — drafted award letters, regret letters, clarifications, all bilingual EN + TE
>
> These four numbers are our **hard sentinels**. Every commit in our migration to Google Cloud preserved them exactly. Hackathon judges can verify by querying our Supabase REST endpoint directly. That's audit defensibility from the first principle."

**On screen:** hover over each stat card. Click "Tab 2 — Validator" — point at "24 Tier-1 typologies live · 154 ValidationFindings".

---

## 1:00 — 1:45 · Demo 2: Module 3 evaluator drilldown (45s)

> "Module 3 is the Post-RFP Evaluator. Click on the **Kurnool** tender — 9 bidders, ECV ₹85 cr.
>
> Effective L1 is **B9 JV** at ₹79.9 cr. Notice: there's a lower bid — B8 at ₹52.7 cr — but that's an **Abnormally Low Bid**. Premium minus 38%. Our `CrossBidAnomalyDetector` flagged it; the `TenderRanking` aggregator applied the ALB-skip rule from `AP-GO-067 §11(4)`; B9 promoted to effective L1.
>
> Each of the 13 Tier-2 verdicts links back to the underlying `BidEvaluationFinding` node_id, and each finding cites the specific clause it tested against. `audit_id` is a 12-char hash, persistent across re-runs. **A CAG auditor can trace any verdict back to a rule_id and a GO citation in under three clicks.**"

**On screen:** open `/module3/tender_synth_kurnool` → scroll the bidder table → click into B8's ALB justification trace → show the citation chain.

---

## 1:45 — 2:30 · Demo 3: Module 4 communications + bilingual toggle (45s)

> "Module 4 is the Communicator. **10 communication types · 75 drafted Communications.**
>
> Filter by type — click `DISQUALIFICATION`. Six letters. Click into one — see the English version. Now toggle to Telugu. **This is rendered by Sarvam-M with DPDP pseudonymisation** — bidder names, PAN, GSTIN, mobile numbers are masked before the API call and restored after. Sarvam-M never sees the real PII.
>
> All bidder-facing communications work this way: award notification, regret letter, disqualification, doc review request, flagged notification, clarification Q&A, bid acknowledgment. Internal communications — like cartel review or internal routing — stay English-only."

**On screen:** open `/module4` → filter `type=DISQUALIFICATION` → click into a Communication → show EN+TE side-by-side.

---

## 2:30 — 3:00 · Demo 4: NEW — Bidder Clarification Q&A in Telugu (30s)

> "And here's the new one. Click **'Submit New Clarification'**. A bidder asks in Telugu: 'PBG శాతం 5% మాత్రమే అడుగుతున్నారా లేక 2.5% ఆమోదిస్తారా?' Submit.
>
> Sarvam-M translates: 'Are you asking about a 5% PBG percentage or 2.5% acceptance?' One new Communication kg_node appears, bilingual.
>
> Officer responds in English citing `AP-GO-175`. Sarvam-M translates back to Telugu. The bidder gets their answer in their language. Audit log captures every step."

**On screen:** open clarification modal → pick Telugu → paste the PBG question → submit → see both languages render in the success card → close → see the new Communication appear in the page list.

---

## Closing line (cue card, no clock)

> "Architecture: 5 Cloud Run services in asia-south1, Cloud Tasks for async dispatch, Google-managed TLS, Cloud Audit Logs to GCS for 400 days. Open source reference systems behind us: ALICE from Brazil's CGU, INACIA from Brazil's TCU, AIPA from Finland's Solita. Our differentiator: AP-state coverage, end-to-end lifecycle, bilingual output, DPDP residency. Code at `github.com/konevenkatesh/procureAI`. Thank you."

---

## Backup beats (if a demo segment breaks)

| Issue | Fallback |
|---|---|
| Custom domain TLS cert expires mid-demo | Switch URL to `https://procure-ai-frontend-mstersp45a-el.a.run.app` |
| Sarvam-M API rate-limited | Use the cached Q&A pair already in Supabase from R4-2b smoke test |
| Cold-start latency on a backend | Pre-warm by visiting each module page during the 0:15 opener |
| Audience asks about VPC SC | "Premium-tier feature; we substituted application-level allowlist + audit logs, documented in `LESSONS_LEARNED.md L98`. VPC SC is on the production roadmap." |
| Audience asks why m1/m2 are stubs | "Module 1 needs a stateful LangGraph checkpointer; Module 2 needs Qdrant migrated to GCP. Both are Phase 2; the API surface is in place today." |

---

## Pre-demo checklist (5 min before)

- [ ] Cert ACTIVE: `gcloud compute ssl-certificates describe procureai-frontend-cert-v2 --global --format="value(managed.status)"`
- [ ] Visit https://procureai.bimsaarthi.com — page loads, green badge visible
- [ ] Open dev tools → Network — refresh — confirm 200 on `/`, `/module1`, `/module2`, `/module3`, `/module4`
- [ ] Click "Submit New Clarification" — modal opens, no console errors
- [ ] Submit a no-op test clarification — Sarvam-M responds < 3s
- [ ] Sentinel snapshot via the verify command in `EXECUTIVE_SUMMARY.md` — confirm 154/351/27/3/6/3/75+ counts
