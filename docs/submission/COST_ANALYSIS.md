# Cost Analysis

## 1. Today's idle posture (managed services in `asia-south1`)

| Line item | ₹/month | Note |
|---|---|---|
| Global external HTTPS Load Balancer (1 forwarding rule + per-rule fee) | 1,800 | Custom domain `procureai.bimsaarthi.com`. Drop to ₹0 if `*.run.app` URL is acceptable. |
| Cloud Run idle (5 services, `min-instances=0`) | 0 | Scale-to-zero |
| Artifact Registry (5 images × ~150 MB) | 15 | |
| Cloud Storage (artifacts + audit-logs buckets) | 5 | |
| Cloud Tasks queue (idle) | 0 | Free tier |
| Cloud Build (when used) | 0 | First 120 build-min/day free; we used ~30 min total in the migration |
| Secret Manager (3 secrets, 2 versions each) | 0 | Free tier |
| Cloud Logging (audit logs at idle) | 100 | First 50 GB free; current burn is well under |
| **Subtotal — idle** | **1,920** | Verified against billing console 2026-05-12 |

## 2. Active-demo posture (during a 1-hour judging session)

| Line item | Marginal ₹ | Note |
|---|---|---|
| Cloud Run requests (judge + 2 watchers) | 1 | ~50 requests at ~1 vCPU-second each |
| Sarvam-M `/translate` calls | 2 | ~10 calls × ₹0.20 each |
| Cloud Tasks dispatches | 0 | Free tier |
| Supabase REST calls | 0 | Free tier |
| **Subtotal — 1-hour demo** | **~3** | Trivial |

## 3. Per-tender end-to-end cost projection

For a single tender going through all 4 modules — Draft → Validate → Evaluate → Communicate:

| Stage | API calls | ₹ per tender |
|---|---|---|
| Module 1: LangGraph drafter (Phase 2) | ~30 OpenRouter calls (qwen-2.5-72b) | 4 |
| Module 2: 24 Tier-1 validators (each: 1 retrieval + 1 LLM grade) | ~50 OpenRouter calls + 24 Qdrant queries | 5 |
| Module 3: 14 Tier-2 + 4 aggregators (Supabase + 1 LLM each on bid validators) | ~14 OpenRouter calls + ~50 Supabase reads | 3 |
| Module 4: 10 communications × 9 bidders × bilingual (Sarvam translate) | ~50 OpenRouter calls + ~90 Sarvam calls | 5 |
| Audit + storage marginal | — | 1 |
| **Per-tender total** | | **~₹18** |

For comparison: a typical CAG tender audit at Big-Four rates is ₹50,000+ per audit. The marginal AI cost is < 0.04% of that.

## 4. Production scale (5,000 tenders/year on AP)

```
5,000 tenders/year ≈ 14 tenders/day average, peaks around 50/day during fiscal year-end.
```

| Line item | ₹/month at scale |
|---|---|
| Cloud Run (some min-instances=1 on hot services) | 2,500 |
| Global HTTPS LB | 1,800 |
| Cloud Storage (artifacts + audit; ~50 GB/year) | 500 |
| Artifact Registry | 50 |
| Cloud Tasks (~150K dispatches/month) | 100 |
| Cloud Build (continuous deploy) | 500 |
| Cloud Logging (audit + app logs, ~30 GB/month) | 2,500 |
| Sarvam-M API (~50K translations/month) | 3,000 |
| OpenRouter LLM (modest 60K calls/month at qwen-2.5-72b) | 4,000 |
| **Total managed-services** | **~₹15,000/month** |

## 5. On-premise alternative (production target — DPDP-strict)

For deployments holding actual government PII at scale:

| Component | Capex | Opex ₹/month |
|---|---|---|
| 4-node Kubernetes cluster (32 vCPU, 128 GB RAM each, 2 TB NVMe) | ₹2.5L | 4,000 (power + cooling) |
| Networking + DC slot | — | 2,000 |
| Operations (sysadmin overhead) | — | 1,500 |
| Local Sarvam-M-24B inference (replaces the API) | included | 0 (marginal API cost = 0) |
| Local Qdrant cluster | included | 0 |
| **Total on-premise** | **₹2.5L one-time** | **~₹8,000/month** |

Crossover point vs cloud: ~30 months. For a 5-year deployment, on-premise wins by ₹3.5L cumulative.

## 6. Two-year total cost of ownership

| Path | Cumulative cost (24 months) |
|---|---|
| Managed services only (`asia-south1`) | ₹3.6L |
| On-premise (with capex amortised) | ₹4.4L |
| **Hybrid: demo + pilot on cloud, prod on-premise from month 6** | **₹3.2L** |

Hybrid is the recommended path: spend ₹1.8L over months 1-6 in the cloud while the AP pilot validates the workflow, then migrate to on-premise once the procurement officer + a department head sign off. Cloud bill drops to LB-only (~₹1,800/month) for the demo URL post-migration.

## 7. Cost reduction levers (post-demo)

1. **Drop the global LB** → save ₹1,800/month if `*.run.app` URL is acceptable for non-customer-facing surfaces.
2. **Use Cloud Run jobs instead of services for /worker** → ₹0 idle (no min-instance cost ever); ~30% cheaper at scale because the LB doesn't see the worker traffic.
3. **Cache OpenRouter responses for identical prompts** → ~50% cut on Module 2/3 LLM cost.
4. **Sarvam-M filesystem cache (already implemented in `scripts/m4_drafters/_sarvam_client.py`)** → first-translation cost only; re-runs are free.
5. **Cloud CDN on the LB** → cuts repeat-visit egress to ~₹100/month at scale.

## 8. What the hackathon billing actually shows

Run-through audit of the actual billing across the 5 GCP sub-blocks (GCP-1 through GCP-5 + R4):

| Sub-block | Real spend |
|---|---|
| GCP-1 (Foundation) | ₹0.00 |
| GCP-2 (4 backend builds + deploys) | ₹0.10 |
| GCP-3 (frontend build + LB stack init) | ₹0.50 |
| GCP-4 (frontend rebuild + wiring) | ₹0.30 |
| GCP-5 (audit bucket + sinks + 2nd cert) | ₹0.05 |
| R4-1+2+3 (m3/m4 rebuilds + frontend rebuild + Sarvam test) | ₹0.20 |
| **Total spend so far** | **~₹1.15** |

Plus 1 day of LB baseline at ₹60/day = ₹60 for the demo period.

---

**Source of truth:** GCP billing console `procureai-prod` project, billing account `019E4D-D4499B-540806`. Budget `ProcureAI Monthly Guard` set at ₹10,000/month with 50/80/100% alerts.
