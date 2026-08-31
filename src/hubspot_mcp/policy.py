"""Risk-tiered approval policy (Bounded Autonomy, Phase 2).

Classifies an interactive write into an approval tier so provably-safe writes
can auto-apply while risky ones keep the full human gate.  Deterministic and
I/O-free in :func:`classify_write` (the loader does the I/O), so the decision
is unit-testable without HubSpot and identical across the daemon, in-process,
and CLI paths.

Tiers (persisted on the pending preview as ``approval_tier``):
- ``AUTO``      — execute immediately; report the result + an undo command.
- ``CONFIRM``   — pause; lightweight ``approve <id>`` (no typed count).
- ``FULL_GATE`` — pause; ``approve <id> <count>`` (typed record count), the
  same ceremony destructive ops get.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hubspot_mcp import config
from hubspot_mcp.models import RiskLevel
from hubspot_mcp.snapshot import is_undoable

AUTO = "AUTO"
CONFIRM = "CONFIRM"
FULL_GATE = "FULL_GATE"

# Shipped conservative default.  Scalar keys (auto_apply_max_records,
# sensitive_property_action) are overridable per-portal, last-wins.  The two
# SAFETY LIST keys (sensitive_properties, never_auto_tools) are UNION-merged
# with these shipped defaults in _coerce: an override can only ADD entries,
# never drop a shipped one — so a well-meant per-portal edit cannot silently
# weaken the workflow gate or unmark a shipped sensitive field.
_DEFAULT_POLICY: dict[str, Any] = {
    "auto_apply_max_records": 100,
    # Pattern-approval backstop (divergence-safe pattern writes): a matched set
    # larger than this requires the typed count at approve, reusing the FULL_GATE
    # count ceremony.  Defaults to auto_apply_max_records when unset (see _coerce).
    "pattern_confirm_threshold": 100,
    # Days a scheduled run's queued (staged) writes stay approvable before the
    # batch auto-expires (Phase 4 scheduled tasks; spec §4/§5).
    "schedule_queue_ttl_days": 7,
    "sensitive_properties": ["amount", "dealstage", "hubspot_owner_id", "lifecyclestage"],
    "sensitive_property_action": "confirm",
    # Side-effectful writes that must ALWAYS keep the human gate even though they
    # are technically reversible: a workflow starts enrolling and acting on
    # contacts the moment it exists, and deleting it later does not undo those
    # actions.  "Deletable" is not "safe to auto-apply."  Editable per portal.
    "never_auto_tools": [
        "hubspot_create_workflow",
        "hubspot_update_workflow",
        "hubspot_toggle_workflow",
        "hubspot_enroll_workflow",
        "hubspot_create_workflow_from_blueprint",
        # PORT DIVERGENCE from hubspot-claude, deliberate. classify_write treats
        # any CREATE as reversible, because a create "undoes by deleting the
        # created record". In this server that undo path is
        # ``hubspot_undo_write`` -> ``hubspot_delete_object(object_type=...)``,
        # which only works for CRM objects. None of the tools below takes an
        # ``object_type``, so target_object is None and the delete cannot run --
        # their AUTO classification rested on a reversibility claim that is
        # false here, and a refund in particular moves money that deleting the
        # refund object would not return. Same reasoning upstream applies to
        # workflows: "deletable" is not "safe to auto-apply".
        "hubspot_create_refund",
        "hubspot_create_form",
        "hubspot_create_report",
        "hubspot_create_dashboard",
    ],
}


@dataclass(frozen=True)
class ApprovalPolicy:
    auto_apply_max_records: int = 100
    pattern_confirm_threshold: int = 100
    schedule_queue_ttl_days: int = 7
    sensitive_properties: tuple[str, ...] = ()
    sensitive_property_action: str = "confirm"  # "confirm" | "full_gate"
    never_auto_tools: tuple[str, ...] = ()

    @property
    def sensitive_tier(self) -> str:
        """Tier a sensitive-property write is routed to."""
        return FULL_GATE if self.sensitive_property_action == "full_gate" else CONFIRM


def _coerce(raw: dict[str, Any]) -> ApprovalPolicy:
    merged = {**_DEFAULT_POLICY, **(raw or {})}
    action = merged.get("sensitive_property_action")
    if action not in ("confirm", "full_gate"):
        action = "confirm"
    try:
        max_records = int(merged.get("auto_apply_max_records", 100))
    except (TypeError, ValueError):
        max_records = 100
    # Shipped default is 100 (= the shipped auto_apply_max_records; spec §7). A
    # malformed override falls back to the effective records ceiling rather than
    # fail-open to "unlimited".
    try:
        pattern_threshold = int(merged.get("pattern_confirm_threshold", max_records))
    except (TypeError, ValueError):
        pattern_threshold = max_records
    # Days a staged scheduled batch stays approvable. A malformed or negative
    # value falls back to the shipped default (7) rather than fail-open.
    try:
        queue_ttl = int(merged.get("schedule_queue_ttl_days", 7))
        if queue_ttl < 0:
            queue_ttl = 7
    except (TypeError, ValueError):
        queue_ttl = 7
    # Union the safety lists with the shipped defaults: an override extends,
    # never replaces, so config cannot drop a shipped protection.  dict.fromkeys
    # dedups while preserving order (shipped entries first).
    props = merged.get("sensitive_properties")
    props = props if isinstance(props, list) else []
    props = list(dict.fromkeys([*_DEFAULT_POLICY["sensitive_properties"], *(str(p) for p in props)]))
    never_auto = merged.get("never_auto_tools")
    never_auto = never_auto if isinstance(never_auto, list) else []
    never_auto = list(dict.fromkeys([*_DEFAULT_POLICY["never_auto_tools"], *(str(t) for t in never_auto)]))
    return ApprovalPolicy(
        auto_apply_max_records=max_records,
        pattern_confirm_threshold=pattern_threshold,
        schedule_queue_ttl_days=queue_ttl,
        sensitive_properties=tuple(props),
        sensitive_property_action=action,
        never_auto_tools=tuple(never_auto),
    )


def _read_json(path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_approval_policy(portal_id: str | None = None) -> ApprovalPolicy:
    """Load the effective policy: shipped default → global → per-portal.

    ``config.CONFIG_DIR`` is read lazily (attribute access) so test fixtures
    that monkeypatch it redirect the reads.  Overrides merge shallow, last
    wins; a malformed/absent file contributes nothing (never fail-open).
    """
    merged = dict(_DEFAULT_POLICY)
    merged.update(_read_json(config.CONFIG_DIR / "approval_policy.json"))
    if portal_id:
        merged.update(_read_json(config.CONFIG_DIR / portal_id / "approval_policy.json"))
    return _coerce(merged)


def _iter_property_keys(payload: dict[str, Any]) -> set[str]:
    """Property names a write would set — single (``properties``) and bulk
    (``records``/``inputs`` of ``{id, properties}``) shapes."""
    keys: set[str] = set()
    props = payload.get("properties")
    if isinstance(props, dict):
        keys.update(props.keys())
    for list_key in ("records", "inputs"):
        items = payload.get(list_key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("properties"), dict):
                    keys.update(item["properties"].keys())
    return keys


def classify_write(preview_data: dict[str, Any], policy: ApprovalPolicy) -> str:
    """Assign a pending write to an approval tier. Pure; first match wins.

    1. destructive (delete/merge)            -> FULL_GATE
    2. reversibility not confirmed           -> FULL_GATE
    3. side-effectful tool (never_auto)      -> CONFIRM
    4. touches a sensitive property          -> policy.sensitive_tier
    5. impact_count > auto_apply_max_records -> CONFIRM
    6. otherwise (safe fast lane)            -> AUTO
    """
    preview = preview_data.get("preview") or {}
    intent = preview_data.get("intent") or {}
    risk = preview.get("risk_level") or intent.get("risk_level")
    intent_type = intent.get("intent_type", "")
    impact = preview.get("impact_count") or 1
    original_values = preview.get("original_values") or {}
    payload = preview_data.get("proposed_payload") or {}
    tool_name = preview_data.get("tool_name")

    if risk == RiskLevel.DESTRUCTIVE.value:
        return FULL_GATE
    if not is_undoable(intent_type, original_values):
        return FULL_GATE
    if tool_name in policy.never_auto_tools:
        return CONFIRM
    # Sensitive-field check: look at both the proposed payload's property keys
    # and the captured original_values' inner keys.  original_values reflects
    # the actually-changed properties for single/bulk updates, so a sensitive
    # field cannot slip into AUTO through a payload shape the matcher doesn't
    # recognize.
    changed_keys = _iter_property_keys(payload)
    for rec in original_values.values():
        if isinstance(rec, dict):
            changed_keys.update(rec.keys())
    if changed_keys & set(policy.sensitive_properties):
        return policy.sensitive_tier
    # Partial-capture guard: an update that captured fewer originals than the
    # records it targets is only PARTIALLY undoable (undo would restore just the
    # captured subset).  Keep a human checkpoint (CONFIRM) rather than AUTO.
    # Single-record updates (impact 1, one captured) and creates are unaffected.
    if intent_type == "update" and len(original_values) < impact:
        return CONFIRM
    if impact > policy.auto_apply_max_records:
        return CONFIRM
    return AUTO
