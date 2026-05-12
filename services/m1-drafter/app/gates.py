"""
M1 gate state machine + RBAC + edit-scope enforcement.

Loaded per directive M1.0 design decisions:
  - 6 active gates: INITIATION → AI_GENERATION → TECHNICAL → FINANCIAL → PROCUREMENT → AUTHORITY → PUBLISHED
  - 5 reviewer roles
  - Send-back rules: TECHNICAL/FINANCIAL/PROCUREMENT revise → back to INITIATION
                     AUTHORITY can sendback to any prior gate
  - Per-gate field edit scope from GATE_EDIT_SCOPE (schemas.py)
"""
from __future__ import annotations

import uuid
from typing import Optional

from .schemas import (
    GATE_EDIT_SCOPE,
    GATE_REVIEWER_ROLE,
    GateAction,
    GateName,
    GateTransitionEdit,
    GateTransitionProps,
    RoleName,
    TenderDraftState,
    now_iso,
)
from .persistence import (
    insert_gate_transition,
    insert_version_snapshot,
    load_tender_draft,
    upsert_tender_draft,
)
from .schemas import DraftVersionSnapshotProps


class GateError(Exception):
    """Raised when a gate action violates the state machine or RBAC rules."""

    def __init__(self, message: str, http_status: int = 403):
        super().__init__(message)
        self.http_status = http_status


# ─── Edit-scope check ────────────────────────────────────────────────


def _path_matches_scope(path: str, scope: list[str]) -> bool:
    """Check if a dot-path falls within a gate's edit scope.

    "*" matches anything. Otherwise the path must equal a scope entry
    or be a sub-path (e.g. scope="boq" matches path="boq" and "boq.0.qty").
    """
    if "*" in scope:
        return True
    for s in scope:
        if path == s:
            return True
        if path.startswith(f"{s}."):
            return True
    return False


def validate_edits(
    edits: list[GateTransitionEdit],
    gate: GateName,
) -> None:
    """Raise GateError if any edit path is outside the gate's scope."""
    scope = GATE_EDIT_SCOPE[gate]
    for e in edits:
        if not _path_matches_scope(e.path, scope):
            raise GateError(
                f"Field '{e.path}' is not editable at gate {gate.value} "
                f"(allowed: {scope or '(read-only)'})",
                http_status=403,
            )


# ─── State-machine transitions ───────────────────────────────────────


# Approve transition map: gate → next gate on approve.
APPROVE_NEXT: dict[GateName, GateName] = {
    GateName.AI_GENERATION: GateName.TECHNICAL,
    GateName.TECHNICAL: GateName.FINANCIAL,
    GateName.FINANCIAL: GateName.PROCUREMENT,
    GateName.PROCUREMENT: GateName.AUTHORITY,
    # AUTHORITY uses PUBLISH not APPROVE
}


def next_gate_on_approve(current: GateName) -> GateName:
    if current not in APPROVE_NEXT:
        raise GateError(
            f"Cannot approve from gate {current.value} (use PUBLISH or REVISE)",
            http_status=409,
        )
    return APPROVE_NEXT[current]


def role_can_act(role: RoleName, gate: GateName) -> bool:
    """Is this role authorised to act on this gate?"""
    expected = GATE_REVIEWER_ROLE.get(gate)
    return expected is not None and expected == role


# ─── Edit application ────────────────────────────────────────────────


def _set_path(d: dict, path: str, value):
    """Set a nested value via dot-path. List indices via numeric segment."""
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        if p.isdigit() and isinstance(cur, list):
            cur = cur[int(p)]
        elif isinstance(cur, dict):
            if p not in cur:
                cur[p] = {}
            cur = cur[p]
        else:
            raise GateError(f"Cannot descend into {path} at segment '{p}'", http_status=400)
    last = parts[-1]
    if last.isdigit() and isinstance(cur, list):
        cur[int(last)] = value
    elif isinstance(cur, dict):
        cur[last] = value
    else:
        raise GateError(f"Cannot set {path}", http_status=400)


def apply_edits(state: TenderDraftState, edits: list[GateTransitionEdit]) -> TenderDraftState:
    """Apply edits to a state — returns a new state object with edits applied.
    Raises GateError if a path cannot be set."""
    d = state.model_dump(mode="json")
    for e in edits:
        _set_path(d, e.path, e.new_value)
    return TenderDraftState.model_validate(d)


# ─── High-level gate actions ─────────────────────────────────────────


def _make_transition_record(
    *,
    draft_id: str,
    from_gate: GateName,
    to_gate: GateName,
    role: RoleName,
    actor_id: str,
    action: GateAction,
    comments: str = "",
    edits: Optional[list[GateTransitionEdit]] = None,
) -> GateTransitionProps:
    return GateTransitionProps(
        transition_id=str(uuid.uuid4()),
        draft_id=draft_id,
        from_gate=from_gate,
        to_gate=to_gate,
        reviewer_role=role,
        reviewer_id=actor_id,
        action=action,
        comments=comments,
        edits=edits or [],
        timestamp=now_iso(),
    )


def _snapshot(state: TenderDraftState, role: RoleName) -> None:
    """Append immutable version snapshot."""
    props = DraftVersionSnapshotProps(
        snapshot_id=str(uuid.uuid4()),
        draft_id=state.draft_id,
        version=state.version,
        payload=state,
        created_by_role=role,
        created_at=now_iso(),
    )
    insert_version_snapshot(props)


def approve(
    draft_id: str,
    actor_role: RoleName,
    actor_id: str,
    comments: str = "",
    edits: Optional[list[GateTransitionEdit]] = None,
) -> TenderDraftState:
    """Approve current gate — bumps state to next gate + snapshots."""
    state = load_tender_draft(draft_id)
    if state is None:
        raise GateError(f"Draft {draft_id} not found", http_status=404)

    if not role_can_act(actor_role, state.current_gate):
        raise GateError(
            f"Role {actor_role.value} cannot act at gate {state.current_gate.value} "
            f"(expected {GATE_REVIEWER_ROLE.get(state.current_gate)})",
            http_status=403,
        )

    if edits:
        validate_edits(edits, state.current_gate)
        state = apply_edits(state, edits)

    next_gate = next_gate_on_approve(state.current_gate)
    from_gate = state.current_gate

    # State transition
    state.current_gate = next_gate
    state.current_assignee_role = GATE_REVIEWER_ROLE.get(next_gate)
    state.version += 1
    state.last_updated_at = now_iso()

    upsert_tender_draft(state)
    insert_gate_transition(_make_transition_record(
        draft_id=draft_id,
        from_gate=from_gate,
        to_gate=next_gate,
        role=actor_role,
        actor_id=actor_id,
        action=GateAction.APPROVE,
        comments=comments,
        edits=edits,
    ))
    _snapshot(state, actor_role)
    return state


def revise(
    draft_id: str,
    actor_role: RoleName,
    actor_id: str,
    comments: str,
) -> TenderDraftState:
    """Revise — sends back to INITIATION for Dealing Officer to re-trigger."""
    if not comments or not comments.strip():
        raise GateError("Comments required for REVISE action", http_status=400)

    state = load_tender_draft(draft_id)
    if state is None:
        raise GateError(f"Draft {draft_id} not found", http_status=404)

    if not role_can_act(actor_role, state.current_gate):
        raise GateError(
            f"Role {actor_role.value} cannot act at gate {state.current_gate.value}",
            http_status=403,
        )

    from_gate = state.current_gate
    state.current_gate = GateName.INITIATION
    state.current_assignee_role = RoleName.DEALING_OFFICER
    state.version += 1
    state.last_updated_at = now_iso()

    upsert_tender_draft(state)
    insert_gate_transition(_make_transition_record(
        draft_id=draft_id,
        from_gate=from_gate,
        to_gate=GateName.INITIATION,
        role=actor_role,
        actor_id=actor_id,
        action=GateAction.REVISE,
        comments=comments,
    ))
    _snapshot(state, actor_role)
    return state


def publish(
    draft_id: str,
    actor_role: RoleName,
    actor_id: str,
    comments: str = "",
) -> TenderDraftState:
    """Authority publish — finalises the draft, assigns tender_id."""
    state = load_tender_draft(draft_id)
    if state is None:
        raise GateError(f"Draft {draft_id} not found", http_status=404)

    if state.current_gate != GateName.AUTHORITY:
        raise GateError(
            f"Cannot publish from gate {state.current_gate.value} (must be AUTHORITY)",
            http_status=409,
        )

    if actor_role != RoleName.TENDER_INVITING_AUTHORITY:
        raise GateError(
            f"Only TENDER_INVITING_AUTHORITY can publish (got {actor_role.value})",
            http_status=403,
        )

    # Synthetic tender_id for demo — production hooks to eGP portal API
    if not state.tender_id:
        # 6-digit numeric like eGP system-assigned IDs (e.g. 933192)
        synthetic_id = str(900000 + (uuid.uuid4().int % 99999))
        state.tender_id = synthetic_id

    state.current_gate = GateName.PUBLISHED
    state.current_assignee_role = None
    state.version += 1
    state.last_updated_at = now_iso()

    upsert_tender_draft(state)
    insert_gate_transition(_make_transition_record(
        draft_id=draft_id,
        from_gate=GateName.AUTHORITY,
        to_gate=GateName.PUBLISHED,
        role=actor_role,
        actor_id=actor_id,
        action=GateAction.PUBLISH,
        comments=comments,
    ))
    _snapshot(state, actor_role)
    return state


def sendback(
    draft_id: str,
    actor_role: RoleName,
    actor_id: str,
    target_gate: GateName,
    comments: str,
) -> TenderDraftState:
    """AUTHORITY-only send-back to any prior gate."""
    if not comments or not comments.strip():
        raise GateError("Comments required for SENDBACK action", http_status=400)

    state = load_tender_draft(draft_id)
    if state is None:
        raise GateError(f"Draft {draft_id} not found", http_status=404)

    if state.current_gate != GateName.AUTHORITY:
        raise GateError(
            f"Sendback only allowed from AUTHORITY (got {state.current_gate.value})",
            http_status=409,
        )

    if actor_role != RoleName.TENDER_INVITING_AUTHORITY:
        raise GateError("Only TENDER_INVITING_AUTHORITY can send back", http_status=403)

    allowed_targets = {
        GateName.INITIATION,
        GateName.TECHNICAL,
        GateName.FINANCIAL,
        GateName.PROCUREMENT,
    }
    if target_gate not in allowed_targets:
        raise GateError(
            f"Cannot sendback to {target_gate.value}; allowed: {[g.value for g in allowed_targets]}",
            http_status=400,
        )

    state.current_gate = target_gate
    state.current_assignee_role = GATE_REVIEWER_ROLE.get(target_gate)
    state.version += 1
    state.last_updated_at = now_iso()

    upsert_tender_draft(state)
    insert_gate_transition(_make_transition_record(
        draft_id=draft_id,
        from_gate=GateName.AUTHORITY,
        to_gate=target_gate,
        role=actor_role,
        actor_id=actor_id,
        action=GateAction.SENDBACK,
        comments=comments,
    ))
    _snapshot(state, actor_role)
    return state
