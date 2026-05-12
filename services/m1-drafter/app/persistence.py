"""
Supabase persistence for M1 — TenderDraft / GateTransition / DraftVersionSnapshot
kg_nodes. Reuses the existing service-role REST pattern from services/_shared/jobs.py.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

import requests

from .schemas import (
    DraftVersionSnapshotProps,
    GateName,
    GateTransitionProps,
    RoleName,
    TenderDraftState,
    now_iso,
)


def _supabase_config() -> tuple[str, dict]:
    url = os.environ.get("SUPABASE_REST_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_REST_URL/SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY required")
    return url, {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _doc_id(draft_id: str) -> str:
    return f"m1_draft_{draft_id}" if not draft_id.startswith("m1_draft_") else draft_id


# ─── TenderDraft kg_node ─────────────────────────────────────────────


def upsert_tender_draft(state: TenderDraftState) -> dict:
    """Insert or update the TenderDraft kg_node. Idempotent on draft_id.

    Returns the inserted/updated kg_node row.
    """
    url, headers = _supabase_config()
    doc_id = _doc_id(state.draft_id)
    payload_dict = state.model_dump(mode="json")
    label = (
        f"M1 Draft: {state.enquiry_particulars.name_of_work[:50]}"
        f" ({state.current_gate.value})"
    )

    # Look up existing
    r = requests.get(
        f"{url}/rest/v1/kg_nodes",
        params={
            "select": "node_id",
            "doc_id": f"eq.{doc_id}",
            "node_type": "eq.TenderDraft",
        },
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()

    if rows:
        # UPDATE
        node_id = rows[0]["node_id"]
        u = requests.patch(
            f"{url}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{node_id}"},
            headers={**headers, "Prefer": "return=representation"},
            json={"label": label, "properties": payload_dict},
            timeout=30,
        )
        u.raise_for_status()
        return u.json()[0]
    else:
        # INSERT
        ins = requests.post(
            f"{url}/rest/v1/kg_nodes",
            headers={**headers, "Prefer": "return=representation"},
            json=[{
                "doc_id": doc_id,
                "node_type": "TenderDraft",
                "label": label,
                "properties": payload_dict,
                "source_ref": "module1:drafter_v1",
            }],
            timeout=30,
        )
        ins.raise_for_status()
        return ins.json()[0]


def load_tender_draft(draft_id: str) -> Optional[TenderDraftState]:
    url, headers = _supabase_config()
    doc_id = _doc_id(draft_id)
    r = requests.get(
        f"{url}/rest/v1/kg_nodes",
        params={
            "select": "properties",
            "doc_id": f"eq.{doc_id}",
            "node_type": "eq.TenderDraft",
        },
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    return TenderDraftState.model_validate(rows[0]["properties"])


# ─── GateTransition kg_node (append-only audit) ──────────────────────


def insert_gate_transition(props: GateTransitionProps) -> dict:
    url, headers = _supabase_config()
    doc_id = _doc_id(props.draft_id)
    label = (
        f"{props.reviewer_role.value}: {props.action.value} "
        f"{props.from_gate.value}→{props.to_gate.value}"
    )
    r = requests.post(
        f"{url}/rest/v1/kg_nodes",
        headers={**headers, "Prefer": "return=representation"},
        json=[{
            "doc_id": doc_id,
            "node_type": "GateTransition",
            "label": label,
            "properties": props.model_dump(mode="json"),
            "source_ref": "module1:gates_v1",
        }],
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]


def list_gate_transitions(draft_id: str) -> list[dict]:
    url, headers = _supabase_config()
    doc_id = _doc_id(draft_id)
    r = requests.get(
        f"{url}/rest/v1/kg_nodes",
        params={
            "select": "node_id,properties,created_at",
            "doc_id": f"eq.{doc_id}",
            "node_type": "eq.GateTransition",
            "order": "created_at.asc",
        },
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ─── DraftVersionSnapshot kg_node (immutable) ────────────────────────


def insert_version_snapshot(props: DraftVersionSnapshotProps) -> dict:
    url, headers = _supabase_config()
    doc_id = _doc_id(props.draft_id)
    label = f"v{props.version} ({props.created_by_role.value})"
    r = requests.post(
        f"{url}/rest/v1/kg_nodes",
        headers={**headers, "Prefer": "return=representation"},
        json=[{
            "doc_id": doc_id,
            "node_type": "DraftVersionSnapshot",
            "label": label,
            "properties": props.model_dump(mode="json"),
            "source_ref": "module1:snapshots_v1",
        }],
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]


def list_version_snapshots(draft_id: str) -> list[dict]:
    url, headers = _supabase_config()
    doc_id = _doc_id(draft_id)
    r = requests.get(
        f"{url}/rest/v1/kg_nodes",
        params={
            "select": "node_id,properties,created_at",
            "doc_id": f"eq.{doc_id}",
            "node_type": "eq.DraftVersionSnapshot",
            "order": "properties->>version.asc",
        },
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ─── Helper for testing / cleanup ────────────────────────────────────


def delete_draft_completely(draft_id: str) -> int:
    """Delete a draft and all its child kg_nodes (audit + snapshots).
    Returns count of rows removed. Idempotent.
    """
    url, headers = _supabase_config()
    doc_id = _doc_id(draft_id)
    r = requests.get(
        f"{url}/rest/v1/kg_nodes",
        params={"select": "node_id", "doc_id": f"eq.{doc_id}"},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    count = 0
    for row in rows:
        d = requests.delete(
            f"{url}/rest/v1/kg_nodes",
            params={"node_id": f"eq.{row['node_id']}"},
            headers=headers,
            timeout=30,
        )
        d.raise_for_status()
        count += 1
    return count
