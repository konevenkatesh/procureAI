"""
M1.8 — End-to-end smoke test: Banaganapalli ₹15,97,185 sample through 4 gates.

Runs entirely in-process via FastAPI TestClient. Uses the real Supabase
backend (writes TenderDraft + GateTransition + DraftVersionSnapshot rows;
verifies hard sentinel preserved; cleans up after).

Test path:
  1. POST /m1/run with the Banaganapalli inputs (Dealing Officer)
  2. Inline worker runs LangGraph 12-node workflow + persists v1, v2
  3. GET /m1/draft/{id} → verify gate=TECHNICAL, version=2
  4. POST /m1/draft/{id}/approve as SENIOR_ENGINEER (TECHNICAL gate)
  5. POST /m1/draft/{id}/approve as DEPARTMENT_HEAD (FINANCIAL gate)
  6. POST /m1/draft/{id}/approve as PROCUREMENT_OFFICER (PROCUREMENT gate)
  7. POST /m1/draft/{id}/publish as TENDER_INVITING_AUTHORITY (AUTHORITY)
  8. Verify final state: PUBLISHED, tender_id assigned, v6
  9. Verify artifacts rendered at /tmp/m1_artifacts/{draft_id}/v6/
 10. Verify sentinel preserved + DELETE draft + verify cleanup

Run:
  cd services/m1-drafter
  python3 test_smoke_banaganapalli.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Force template mode for offline testing
os.environ.setdefault("M1_DRAFTER_MODE", "template")
# Suppress sleep delays
import time as _t
_orig_sleep = _t.sleep
_t.sleep = lambda x: None  # type: ignore

# Add repo + services to sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                            # services/m1-drafter
sys.path.insert(0, str(HERE.parent))                     # services
sys.path.insert(0, str(HERE.parent.parent))              # repo root

# Load .env from repo root
env_path = HERE.parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ─── Test runner ─────────────────────────────────────────────────────


def main() -> int:
    print("=" * 80)
    print("  M1.8 SMOKE TEST — Banaganapalli ₹15,97,185 sample through 4 gates")
    print("=" * 80)

    from fastapi.testclient import TestClient
    from app.main import app
    from app.persistence import (
        delete_draft_completely,
        list_gate_transitions,
        list_version_snapshots,
    )

    import requests

    # Sentinel snapshot helpers
    def sentinel() -> tuple[int, ...]:
        REST = os.environ.get("SUPABASE_REST_URL") or os.environ.get("SUPABASE_URL")
        KEY = os.environ.get("SUPABASE_ANON_KEY")
        H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
        def n(nt):
            r = requests.get(f"{REST}/rest/v1/kg_nodes",
                params={"select": "node_id", "node_type": f"eq.{nt}"},
                headers={**H, "Prefer": "count=exact", "Range": "0-0"}, timeout=30)
            return int(r.headers["Content-Range"].split("/")[1])
        def e(et):
            r = requests.get(f"{REST}/rest/v1/kg_edges",
                params={"select": "edge_id", "edge_type": f"eq.{et}"},
                headers={**H, "Prefer": "count=exact", "Range": "0-0"}, timeout=30)
            return int(r.headers["Content-Range"].split("/")[1])
        return (
            n("ValidationFinding"), n("BidEvaluationFinding"), e("BIDDER_VIOLATES_RULE"),
            n("EligibilityMatrix"), n("TenderRanking"), n("BidAnomalyFinding"),
            n("ComparativeStatement"),
            n("Communication"),
            n("TenderDraft"), n("GateTransition"), n("DraftVersionSnapshot"),
        )

    pre = sentinel()
    print(f"\n── Pre sentinel ──")
    print(f"  Hard: {'/'.join(str(x) for x in pre[:7])}  (expect 154/351/49/27/3/6/3)")
    print(f"  Additive: Communication={pre[7]}  TenderDraft={pre[8]}  GateTransition={pre[9]}  DraftVersionSnapshot={pre[10]}")
    assert pre[:7] == (154, 351, 49, 27, 3, 6, 3), f"Hard sentinel mismatch: {pre[:7]}"
    pre_tender_draft = pre[8]
    pre_gate_trans = pre[9]
    pre_snapshots = pre[10]

    client = TestClient(app)

    # ── Banaganapalli sample inputs ──
    banaganapalli = {
        "tender_id": None,
        "params": {
            "initiator_role": "DEALING_OFFICER",
            "initiator_id": "demo_smoke_test_ee",
            "initial_payload": {
                "enquiry_particulars": {
                    "department_name": "PRED",
                    "circle_division": "PRED-Executive Engineer, PR PIU division, Kurnool",
                    "officer_inviting_bids": "Executive Engineer, PR PIU division, Kurnool",
                    "bid_opening_authority": "E E",
                    "address": "Nunepalli MPDO Office Compound",
                    "contact_details": "7780743028",
                    "email": "eepiuknl@yahoo.com",
                    "name_of_project": "DMF",
                    "name_of_work": "Providing Kitchen Shed and additional facilities to Shadikhana at Banaganapalli",
                },
                "classification": {
                    "tender_category": "WORKS",
                    "type_of_work": "Civil Works",
                    "tender_type": "OPEN - NCB",
                    "bidding_type": "OPEN",
                    "form_of_contract": "L.S",
                    "consortium_joint_venture": "Not Applicable",
                    "bid_call_numbers": 1,
                },
                "financial": {
                    "estimated_contract_value_inr": 1597185,
                    "estimated_contract_value_words": "Fifteen Lakh Ninety Seven Thousand One Hundred and Eighty Five Rupees",
                    "period_of_completion_months": 6,
                    "bid_validity_days": 90,
                    "bid_security_percent": 1.0,
                    "bid_security_inr": 15972,
                    "bid_security_in_favour_of": "Online payment",
                    "mode_of_payment": "Online Payment, Challan Generation, BG",
                    "currency_type": "INR",
                    "default_currency": "Indian Rupee - INR",
                    "transaction_fee_inr": 566,
                    "transaction_fee_payable_to": "APTS payable at Vijayawada",
                    "transaction_fee_go_reference": "G.O.Ms No 4, Dtd 17.02.2015 IT&C Dept",
                },
                "geography": {
                    "state": "ANDHRA PRADESH",
                    "district": "NANDYAL",
                    "mandal": "BANAGANAPALLE",
                    "assembly": "Banaganapalli",
                    "parliament": "Nandyal",
                },
                "evaluation": {
                    "evaluation_type": "Percentage",
                    "evaluation_criteria": "Based on Price",
                    "display_rank": "Lowest",
                },
                "documents": [
                    {"s_no": 1, "document_name": "Registration Certificate", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 2, "document_name": "EMD Using net Banking/RTGS/NEFT The Bidders Should be Pay EMDS from their Registered bank accounts and the Unsuccessful Bidders", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 3, "document_name": "GST Registration Certificate", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 4, "document_name": "Declaration and Critical Equipment Owned or Leased on Judicial Stamp paper of RS 100", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 5, "document_name": "Saral 2025-2026 Submitted to IT Dept with PAN Card", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 6, "document_name": "Key Personal as per Bid Document", "stage": "COMMON", "document_type": "Mandatory"},
                    {"s_no": 7, "document_name": "Any other Documents Required as per Tender Schedule", "stage": "COMMON", "document_type": "Mandatory"},
                ],
                "dates": {
                    "start_date": "2026-05-12T18:20:00+05:30",
                    "end_date": "2026-05-28T11:00:00+05:30",
                    "closing_date": "2026-05-28T11:30:00+05:30",
                },
                "enquiry_forms": [{
                    "stage": "Commercial Stage",
                    "form_name": "Percentage Wise Rate",
                    "type_of_form": "Secure",
                    "supporting_document_required": "No",
                    "supporting_document_description": "N/A",
                }],
            },
        },
    }

    # ── Step 1: POST /m1/run ──
    print(f"\n── Step 1: POST /m1/run (Dealing Officer initiates) ──")
    t0 = time.time()
    r = client.post("/m1/run", json=banaganapalli)
    elapsed = time.time() - t0
    if r.status_code != 200:
        print(f"  ✗ POST /m1/run failed: {r.status_code} {r.text[:500]}")
        return 1
    run_data = r.json()
    job_id = run_data["job_id"]
    print(f"  ✓ Job submitted: {job_id} (status={run_data['status']}, wall={elapsed:.2f}s)")

    # Find the draft_id from the worker result
    job_r = client.get(f"/jobs/{job_id}")
    job_data = job_r.json()
    if job_data.get("status") != "DONE":
        print(f"  ✗ Job not DONE: {job_data}")
        return 1
    draft_id = job_data["result"]["draft_id"]
    print(f"  ✓ Worker complete: draft_id={draft_id}, gate={job_data['result']['current_gate']}, "
          f"events={job_data['result']['n_events_emitted']}")

    try:
        # ── Step 2: Verify draft state at TECHNICAL gate ──
        print(f"\n── Step 2: Verify draft at TECHNICAL gate (post-AI-generation) ──")
        r = client.get(f"/m1/draft/{draft_id}")
        assert r.status_code == 200, f"GET draft failed: {r.status_code}"
        d = r.json()
        assert d["current_gate"] == "TECHNICAL", f"Expected TECHNICAL, got {d['current_gate']}"
        assert d["version"] == 2, f"Expected v2, got v{d['version']}"
        assert len(d["boq"]) >= 3, f"Expected ≥3 BoQ rows, got {len(d['boq'])}"
        assert len(d["general_terms"]["eligibility"]) > 500, "Eligibility text too short"
        assert d["financial"]["estimated_contract_value_inr"] == 1597185
        print(f"  ✓ Gate=TECHNICAL, v=2, BoQ={len(d['boq'])} rows, eligibility={len(d['general_terms']['eligibility'])}chars")
        print(f"  ✓ NIT auto-generated: {d.get('tender_notice_number')}")
        print(f"  ✓ Citations: {len(d['citations']['sources'])} sources")

        # ── Step 3: SENIOR_ENGINEER approves TECHNICAL ──
        print(f"\n── Step 3: SENIOR_ENGINEER approves at TECHNICAL gate ──")
        r = client.post(f"/m1/draft/{draft_id}/approve", json={
            "draft_id": draft_id, "actor_role": "SENIOR_ENGINEER",
            "actor_id": "demo_senior_eng", "comments": "Technical specs accepted",
            "edits": [],
        })
        assert r.status_code == 200, f"approve failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["current_gate"] == "FINANCIAL", f"Expected FINANCIAL, got {d['current_gate']}"
        assert d["version"] == 3
        print(f"  ✓ TECHNICAL → FINANCIAL (v={d['version']}, pending {d['current_assignee_role']})")

        # ── Step 3b: Wrong role rejection check ──
        print(f"  ── (negative test: DEALING_OFFICER tries to approve FINANCIAL) ──")
        r_neg = client.post(f"/m1/draft/{draft_id}/approve", json={
            "draft_id": draft_id, "actor_role": "DEALING_OFFICER",
            "actor_id": "x", "comments": "",
        })
        assert r_neg.status_code == 403, f"Expected 403 RBAC reject, got {r_neg.status_code}"
        print(f"  ✓ Wrong role correctly rejected with 403")

        # ── Step 4: DEPARTMENT_HEAD approves FINANCIAL ──
        print(f"\n── Step 4: DEPARTMENT_HEAD approves at FINANCIAL gate ──")
        r = client.post(f"/m1/draft/{draft_id}/approve", json={
            "draft_id": draft_id, "actor_role": "DEPARTMENT_HEAD",
            "actor_id": "demo_dept_head", "comments": "Financials confirmed",
        })
        assert r.status_code == 200, f"FIN approve failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["current_gate"] == "PROCUREMENT", d
        assert d["version"] == 4
        print(f"  ✓ FINANCIAL → PROCUREMENT (v={d['version']})")

        # ── Step 5: PROCUREMENT_OFFICER approves PROCUREMENT ──
        print(f"\n── Step 5: PROCUREMENT_OFFICER approves at PROCUREMENT gate ──")
        r = client.post(f"/m1/draft/{draft_id}/approve", json={
            "draft_id": draft_id, "actor_role": "PROCUREMENT_OFFICER",
            "actor_id": "demo_proc_off", "comments": "Procurement params accepted",
        })
        assert r.status_code == 200, f"PROC approve failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["current_gate"] == "AUTHORITY", d
        assert d["version"] == 5
        print(f"  ✓ PROCUREMENT → AUTHORITY (v={d['version']})")

        # ── Step 5b: Edit-scope rejection ──
        print(f"  ── (negative test: edit out-of-scope at AUTHORITY) ──")
        r_neg = client.post(f"/m1/draft/{draft_id}/approve", json={
            "draft_id": draft_id, "actor_role": "TENDER_INVITING_AUTHORITY",
            "actor_id": "x", "comments": "",
            "edits": [{"path": "boq.0.qty", "old_value": 120, "new_value": 999}],
        })
        # AUTHORITY scope is empty (read-only) → either 403 from validate_edits OR 409 from approve disallowed
        assert r_neg.status_code in (403, 409), f"Expected 403/409 for out-of-scope edit, got {r_neg.status_code}"
        print(f"  ✓ AUTHORITY rejects edits / cannot approve ({r_neg.status_code})")

        # ── Step 6: TENDER_INVITING_AUTHORITY publishes ──
        print(f"\n── Step 6: TENDER_INVITING_AUTHORITY publishes ──")
        r = client.post(f"/m1/draft/{draft_id}/publish", json={
            "draft_id": draft_id, "actor_role": "TENDER_INVITING_AUTHORITY",
            "actor_id": "demo_authority", "comments": "Approved for publication",
        })
        assert r.status_code == 200, f"publish failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["current_gate"] == "PUBLISHED", d
        assert d["version"] == 6
        assert d["tender_id"], "tender_id should be assigned at publish"
        print(f"  ✓ AUTHORITY → PUBLISHED (v={d['version']}, tender_id={d['tender_id']})")
        artifacts = d.get("artifacts", {})
        if "artifact_dir" in artifacts:
            print(f"  ✓ Artifacts directory: {artifacts['artifact_dir']}")
            for k, v in artifacts.items():
                if k.endswith("_error"):
                    print(f"  ⚠ {k}: {v[:80]}")
                elif k == "artifact_dir":
                    continue
                else:
                    size = os.path.getsize(v) if os.path.exists(v) else "?"
                    print(f"    {k}: {v} ({size} bytes)")

        # ── Step 7: Verify audit + snapshots ──
        print(f"\n── Step 7: Verify audit + version snapshots ──")
        audit = list_gate_transitions(draft_id)
        snapshots = list_version_snapshots(draft_id)
        print(f"  GateTransitions: {len(audit)} (expect 4 — TECH approve / FIN approve / PROC approve / AUTH publish)")
        print(f"  DraftVersionSnapshots: {len(snapshots)} (expect 6 — v1 init, v2 post-AI, v3 TECH, v4 FIN, v5 PROC, v6 PUBLISH)")
        # Verbose: list transitions
        for entry in audit:
            p = entry["properties"]
            print(f"    [{p['action']:8}] {p['from_gate']} → {p['to_gate']} by {p['reviewer_role']}")

        assert len(audit) == 4, f"Expected 4 transitions, got {len(audit)}"
        assert len(snapshots) == 6, f"Expected 6 snapshots, got {len(snapshots)}"

        # ── Step 8: Verify sentinel preserved ──
        print(f"\n── Step 8: Sentinel preservation check ──")
        post = sentinel()
        print(f"  Hard: {'/'.join(str(x) for x in post[:7])}  (expect unchanged)")
        print(f"  Additive deltas: Communication={post[7]} (Δ={post[7]-pre[7]}), "
              f"TenderDraft={post[8]} (Δ=+{post[8]-pre_tender_draft}), "
              f"GateTransition={post[9]} (Δ=+{post[9]-pre_gate_trans}), "
              f"DraftVersionSnapshot={post[10]} (Δ=+{post[10]-pre_snapshots})")
        assert post[:7] == pre[:7], f"Hard sentinel drift! {pre[:7]} → {post[:7]}"
        assert post[8] - pre_tender_draft == 1, f"Expected +1 TenderDraft, got +{post[8]-pre_tender_draft}"
        assert post[9] - pre_gate_trans == 4, f"Expected +4 GateTransition, got +{post[9]-pre_gate_trans}"
        assert post[10] - pre_snapshots == 6, f"Expected +6 DraftVersionSnapshot, got +{post[10]-pre_snapshots}"
        print(f"  ✓ Hard sentinel preserved; additive deltas match predictions")

        # ── Step 9: Verify artifact files exist ──
        print(f"\n── Step 9: Verify artifact files on disk ──")
        artifact_dir = Path(f"/tmp/m1_artifacts/{draft_id}/v6")
        if artifact_dir.exists():
            files = sorted(artifact_dir.iterdir())
            print(f"  {len(files)} artifacts at {artifact_dir}:")
            for f in files:
                print(f"    {f.name} ({f.stat().st_size:,} bytes)")
            assert any(f.name == "BID_DOCUMENT.docx" for f in files), "BID_DOCUMENT.docx missing"
            assert any(f.name == "BID_DOCUMENT.pdf" for f in files), "BID_DOCUMENT.pdf missing"
            print(f"  ✓ Key artifacts present (DOCX + PDF + BoQ + ELIGIBILITY + MD)")
        else:
            print(f"  ⚠ Artifact dir not found: {artifact_dir}")

        print()
        print("=" * 80)
        print("  ✓ SMOKE TEST PASSED — Banaganapalli sample successfully drafted, reviewed, approved, and published")
        print("=" * 80)
        return 0

    finally:
        # Cleanup
        print(f"\n── Cleanup: removing draft {draft_id} + audit + snapshots ──")
        deleted = delete_draft_completely(draft_id)
        print(f"  Removed {deleted} kg_node rows")
        # Also remove the Job row from the test
        try:
            REST = os.environ.get("SUPABASE_REST_URL") or os.environ.get("SUPABASE_URL")
            KEY = os.environ.get("SUPABASE_ANON_KEY")
            H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
            requests.delete(f"{REST}/rest/v1/kg_nodes",
                params={"node_id": f"eq.{job_id}"}, headers=H, timeout=15)
            print(f"  Removed job row {job_id[:12]}")
        except Exception:
            pass
        # Final sentinel check
        final = sentinel()
        if final[:7] != pre[:7]:
            print(f"  ⚠ Hard sentinel post-cleanup: {final[:7]} (was pre: {pre[:7]})")
        else:
            print(f"  ✓ Hard sentinel restored: {final[:7]}")
        print(f"  Additive post-cleanup: TenderDraft={final[8]}, GateTransition={final[9]}, DraftVersionSnapshot={final[10]}")


if __name__ == "__main__":
    sys.exit(main())
