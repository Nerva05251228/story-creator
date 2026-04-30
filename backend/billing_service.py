from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import models
from database import SessionLocal


MONEY_QUANT = Decimal("0.00001")
BILLING_MONTH_TIMEZONE = ZoneInfo("Asia/Shanghai")

LEGACY_FLAT_IMAGE_RULE_NAMES = {
    "banana2 image",
    "banana-pro image",
}
LEGACY_TEXT_RULE_NAMES = {
    "OpenRouter text",
    "YYDS text",
}

DEFAULT_BILLING_RULES = [
    {
        "rule_name": "banana2 image 1k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana2",
        "resolution": "1k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.06"),
        "priority": 100,
    },
    {
        "rule_name": "banana2 image 2k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana2",
        "resolution": "2k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.07"),
        "priority": 100,
    },
    {
        "rule_name": "banana2 image 4k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana2",
        "resolution": "4k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.08"),
        "priority": 100,
    },
    {
        "rule_name": "banana-pro image 1k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana-pro",
        "resolution": "1k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.12"),
        "priority": 100,
    },
    {
        "rule_name": "banana-pro image 2k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana-pro",
        "resolution": "2k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.14"),
        "priority": 100,
    },
    {
        "rule_name": "banana-pro image 4k",
        "category": "image",
        "stage": "",
        "provider": "banana",
        "model_name": "banana-pro",
        "resolution": "4k",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0.20"),
        "priority": 100,
    },
    {
        "rule_name": "jimeng free image",
        "category": "image",
        "stage": "",
        "provider": "jimeng",
        "model_name": "图片 4.6",
        "resolution": "",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0"),
        "priority": 100,
    },
    {
        "rule_name": "banana2 moti free image",
        "category": "image",
        "stage": "",
        "provider": "moti",
        "model_name": "banana2-moti",
        "resolution": "",
        "billing_mode": "per_image",
        "unit_price_rmb": Decimal("0"),
        "priority": 100,
    },
    {
        "rule_name": "grok video",
        "category": "video",
        "stage": "",
        "provider": "yijia",
        "model_name": "grok",
        "resolution": "",
        "billing_mode": "per_second",
        "unit_price_rmb": Decimal("0.049"),
        "priority": 100,
    },
]


def _normalize_text(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "yijia-grok":
        return "yijia"
    return normalized


def _normalize_resolution(value: Optional[str]) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    aliases = {
        "1024": "1k",
        "1k": "1k",
        "2k": "2k",
        "2048": "2k",
        "4k": "4k",
        "4096": "4k",
    }
    return aliases.get(normalized, normalized)


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _quantize_money(value: Decimal) -> Decimal:
    return _to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _format_money(value: Any) -> str:
    return format(_quantize_money(_to_decimal(value)), ".5f")


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    try:
        return value.isoformat()
    except Exception:
        return None


def _format_month_key(value: Optional[datetime]) -> str:
    if not value:
        return ""
    try:
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        return current.astimezone(BILLING_MONTH_TIMEZONE).strftime("%Y-%m")
    except Exception:
        return ""


def _normalize_billing_month(month: Optional[str]) -> str:
    normalized = str(month or "").strip()
    if not normalized:
        return ""
    try:
        return datetime.strptime(normalized, "%Y-%m").strftime("%Y-%m")
    except Exception:
        return ""


def _get_billing_month_utc_range(month: Optional[str]) -> Optional[tuple[datetime, datetime]]:
    normalized = _normalize_billing_month(month)
    if not normalized:
        return None
    year, month_value = normalized.split("-")
    start_local = datetime(int(year), int(month_value), 1, tzinfo=BILLING_MONTH_TIMEZONE)
    if int(month_value) == 12:
        end_local = datetime(int(year) + 1, 1, 1, tzinfo=BILLING_MONTH_TIMEZONE)
    else:
        end_local = datetime(int(year), int(month_value) + 1, 1, tzinfo=BILLING_MONTH_TIMEZONE)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _parse_detail_json(raw_value: Any) -> Optional[Any]:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None
    try:
        return json.loads(normalized)
    except Exception:
        return None


def _build_amount_bucket() -> Dict[str, Any]:
    return {
        "charge_count": 0,
        "refund_count": 0,
        "gross_amount_rmb": Decimal("0"),
        "refund_amount_rmb": Decimal("0"),
        "net_amount_rmb": Decimal("0"),
        "pending_amount_rmb": Decimal("0"),
        "finalized_amount_rmb": Decimal("0"),
        "reversed_amount_rmb": Decimal("0"),
    }


def _serialize_amount_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "charge_count": int(bucket["charge_count"]),
        "refund_count": int(bucket["refund_count"]),
        "gross_amount_rmb": _format_money(bucket["gross_amount_rmb"]),
        "refund_amount_rmb": _format_money(bucket["refund_amount_rmb"]),
        "net_amount_rmb": _format_money(bucket["net_amount_rmb"]),
        "pending_amount_rmb": _format_money(bucket["pending_amount_rmb"]),
        "finalized_amount_rmb": _format_money(bucket["finalized_amount_rmb"]),
        "reversed_amount_rmb": _format_money(bucket["reversed_amount_rmb"]),
    }


def _apply_entry_to_bucket(bucket: Dict[str, Any], entry: models.BillingLedgerEntry):
    amount = _to_decimal(entry.amount_rmb)
    entry_type = str(entry.entry_type or "").strip().lower()
    status = str(entry.status or "").strip().lower()

    if entry_type == "refund":
        bucket["refund_count"] += 1
        bucket["refund_amount_rmb"] += amount
    else:
        bucket["charge_count"] += 1
        bucket["gross_amount_rmb"] += amount
        if status == "pending":
            bucket["pending_amount_rmb"] += amount
        elif status == "reversed":
            bucket["reversed_amount_rmb"] += amount
        else:
            bucket["finalized_amount_rmb"] += amount

    bucket["net_amount_rmb"] += amount


def _serialize_ledger_entry(entry: models.BillingLedgerEntry) -> Dict[str, Any]:
    return {
        "id": int(entry.id),
        "created_at": _format_datetime(entry.created_at),
        "user_id": int(entry.user_id),
        "script_id": int(entry.script_id),
        "episode_id": int(entry.episode_id),
        "shot_id": int(entry.shot_id) if entry.shot_id is not None else None,
        "storyboard2_shot_id": int(entry.storyboard2_shot_id) if entry.storyboard2_shot_id is not None else None,
        "sub_shot_id": int(entry.sub_shot_id) if entry.sub_shot_id is not None else None,
        "card_id": int(entry.card_id) if entry.card_id is not None else None,
        "dashboard_task_log_id": int(entry.dashboard_task_log_id) if entry.dashboard_task_log_id is not None else None,
        "category": str(entry.category or ""),
        "stage": str(entry.stage or ""),
        "provider": str(entry.provider or ""),
        "model_name": str(entry.model_name or ""),
        "resolution": str(getattr(entry, "resolution", "") or ""),
        "billing_mode": str(entry.billing_mode or ""),
        "quantity": _format_money(entry.quantity),
        "unit_price_rmb": _format_money(entry.unit_price_rmb),
        "amount_rmb": _format_money(entry.amount_rmb),
        "entry_type": str(entry.entry_type or ""),
        "status": str(entry.status or ""),
        "billing_key": str(entry.billing_key or ""),
        "operation_key": str(entry.operation_key or ""),
        "attempt_index": int(entry.attempt_index or 1),
        "external_task_id": str(entry.external_task_id or ""),
        "reason": str(entry.reason or ""),
        "detail_json": str(entry.detail_json or ""),
        "detail": _parse_detail_json(entry.detail_json),
        "parent_entry_id": int(entry.parent_entry_id) if entry.parent_entry_id is not None else None,
    }


def serialize_price_rule(rule: models.BillingPriceRule) -> Dict[str, Any]:
    return {
        "id": int(rule.id),
        "rule_name": str(rule.rule_name or ""),
        "category": str(rule.category or ""),
        "stage": str(rule.stage or ""),
        "provider": str(rule.provider or ""),
        "model_name": str(rule.model_name or ""),
        "resolution": str(getattr(rule, "resolution", "") or ""),
        "billing_mode": str(rule.billing_mode or ""),
        "unit_price_rmb": _format_money(rule.unit_price_rmb),
        "is_active": bool(rule.is_active),
        "priority": int(rule.priority or 0),
        "effective_from": _format_datetime(rule.effective_from),
        "effective_to": _format_datetime(rule.effective_to),
        "created_at": _format_datetime(rule.created_at),
        "updated_at": _format_datetime(rule.updated_at),
    }


def ensure_default_pricing_rules(db) -> int:
    created_count = 0
    updated_count = 0

    legacy_rules = db.query(models.BillingPriceRule).filter(
        models.BillingPriceRule.rule_name.in_(LEGACY_FLAT_IMAGE_RULE_NAMES | LEGACY_TEXT_RULE_NAMES)
    ).all()
    for rule in legacy_rules:
        if rule.is_active:
            rule.is_active = False
            updated_count += 1

    for item in DEFAULT_BILLING_RULES:
        existing = db.query(models.BillingPriceRule).filter(
            models.BillingPriceRule.rule_name == item["rule_name"]
        ).first()
        if existing:
            changed = False
            for field_name, field_value in (
                ("category", item["category"]),
                ("stage", item["stage"]),
                ("provider", item["provider"]),
                ("model_name", item["model_name"]),
                ("resolution", item["resolution"]),
                ("billing_mode", item["billing_mode"]),
                ("unit_price_rmb", item["unit_price_rmb"]),
                ("is_active", True),
                ("priority", item["priority"]),
            ):
                if getattr(existing, field_name) != field_value:
                    setattr(existing, field_name, field_value)
                    changed = True
            if changed:
                updated_count += 1
            continue
        db.add(models.BillingPriceRule(
            rule_name=item["rule_name"],
            category=item["category"],
            stage=item["stage"],
            provider=item["provider"],
            model_name=item["model_name"],
            resolution=item["resolution"],
            billing_mode=item["billing_mode"],
            unit_price_rmb=item["unit_price_rmb"],
            is_active=True,
            priority=item["priority"],
        ))
        created_count += 1
    if created_count or updated_count:
        db.flush()
    return created_count + updated_count


def get_price_rules(db) -> List[Dict[str, Any]]:
    rows = db.query(models.BillingPriceRule).filter(
        ~models.BillingPriceRule.rule_name.in_(list(LEGACY_FLAT_IMAGE_RULE_NAMES | LEGACY_TEXT_RULE_NAMES))
    ).order_by(
        models.BillingPriceRule.is_active.desc(),
        models.BillingPriceRule.category.asc(),
        models.BillingPriceRule.priority.desc(),
        models.BillingPriceRule.id.asc(),
    ).all()
    return [serialize_price_rule(row) for row in rows]


def create_price_rule(
    db,
    *,
    rule_name: str,
    category: str,
    billing_mode: str,
    unit_price_rmb: Any,
    stage: str = "",
    provider: str = "",
    model_name: str = "",
    resolution: str = "",
    is_active: bool = True,
    priority: int = 0,
    effective_from: Optional[datetime] = None,
    effective_to: Optional[datetime] = None,
):
    row = models.BillingPriceRule(
        rule_name=str(rule_name or "").strip(),
        category=_normalize_text(category),
        stage=_normalize_text(stage),
        provider=_normalize_text(provider),
        model_name=_normalize_text(model_name),
        resolution=_normalize_resolution(resolution),
        billing_mode=str(billing_mode or "").strip(),
        unit_price_rmb=_quantize_money(_to_decimal(unit_price_rmb)),
        is_active=bool(is_active),
        priority=int(priority or 0),
        effective_from=effective_from,
        effective_to=effective_to,
    )
    db.add(row)
    db.flush()
    return row


def update_price_rule(
    db,
    *,
    rule_id: int,
    rule_name: Optional[str] = None,
    category: Optional[str] = None,
    billing_mode: Optional[str] = None,
    unit_price_rmb: Any = None,
    stage: Optional[str] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    resolution: Optional[str] = None,
    is_active: Optional[bool] = None,
    priority: Optional[int] = None,
    effective_from: Any = "__KEEP__",
    effective_to: Any = "__KEEP__",
):
    row = db.query(models.BillingPriceRule).filter(
        models.BillingPriceRule.id == int(rule_id)
    ).first()
    if not row:
        return None

    if rule_name is not None:
        row.rule_name = str(rule_name or "").strip()
    if category is not None:
        row.category = _normalize_text(category)
    if stage is not None:
        row.stage = _normalize_text(stage)
    if provider is not None:
        row.provider = _normalize_text(provider)
    if model_name is not None:
        row.model_name = _normalize_text(model_name)
    if resolution is not None:
        row.resolution = _normalize_resolution(resolution)
    if billing_mode is not None:
        row.billing_mode = str(billing_mode or "").strip()
    if unit_price_rmb is not None:
        row.unit_price_rmb = _quantize_money(_to_decimal(unit_price_rmb))
    if is_active is not None:
        row.is_active = bool(is_active)
    if priority is not None:
        row.priority = int(priority or 0)
    if effective_from != "__KEEP__":
        row.effective_from = effective_from
    if effective_to != "__KEEP__":
        row.effective_to = effective_to

    db.flush()
    return row


def _get_episode_and_script(db, episode_id: Optional[int]):
    if not episode_id:
        return None, None
    episode = db.query(models.Episode).filter(models.Episode.id == int(episode_id)).first()
    if not episode:
        return None, None
    script = db.query(models.Script).filter(models.Script.id == int(episode.script_id)).first()
    if not script:
        return episode, None
    return episode, script


def _episode_supports_billing(db, episode_id: Optional[int]) -> bool:
    episode, _script = _get_episode_and_script(db, episode_id)
    if not episode:
        return False
    return int(getattr(episode, "billing_version", 0) or 0) >= 1


def get_episode_context(db, *, episode_id: int) -> Optional[Dict[str, Any]]:
    episode, script = _get_episode_and_script(db, episode_id)
    if not episode or not script:
        return None
    return {
        "episode_id": int(episode.id),
        "script_id": int(script.id),
        "user_id": int(script.user_id),
        "episode_name": str(episode.name or ""),
        "script_name": str(script.name or ""),
        "billing_version": int(getattr(episode, "billing_version", 0) or 0),
    }


def get_card_episode_context(db, *, card_id: int) -> Optional[Dict[str, Any]]:
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == int(card_id)).first()
    if not card:
        return None
    library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == int(card.library_id)).first()
    if not library or not library.episode_id:
        return None
    context = get_episode_context(db, episode_id=int(library.episode_id))
    if not context:
        return None
    context["card_id"] = int(card.id)
    context["library_id"] = int(library.id)
    return context


def get_shot_episode_context(db, *, shot_id: int) -> Optional[Dict[str, Any]]:
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == int(shot_id)).first()
    if not shot:
        return None
    context = get_episode_context(db, episode_id=int(shot.episode_id))
    if not context:
        return None
    context["shot_id"] = int(shot.id)
    context["shot_number"] = int(getattr(shot, "shot_number", 0) or 0)
    return context


def get_storyboard2_sub_shot_context(db, *, sub_shot_id: int) -> Optional[Dict[str, Any]]:
    sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == int(sub_shot_id)
    ).first()
    if not sub_shot:
        return None
    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == int(sub_shot.storyboard2_shot_id)
    ).first()
    if not storyboard2_shot:
        return None
    context = get_episode_context(db, episode_id=int(storyboard2_shot.episode_id))
    if not context:
        return None
    context["storyboard2_shot_id"] = int(storyboard2_shot.id)
    context["sub_shot_id"] = int(sub_shot.id)
    context["sub_shot_index"] = int(getattr(sub_shot, "sub_shot_index", 0) or 0)
    return context


def resolve_price_rule(
    db,
    *,
    category: str,
    stage: str = "",
    provider: str = "",
    model_name: str = "",
    resolution: str = "",
    at_time: Optional[datetime] = None,
):
    current_time = at_time or datetime.utcnow()
    normalized_category = _normalize_text(category)
    normalized_stage = _normalize_text(stage)
    normalized_provider = _normalize_text(provider)
    normalized_model = _normalize_text(model_name)
    normalized_resolution = _normalize_resolution(resolution)

    candidates = db.query(models.BillingPriceRule).filter(
        models.BillingPriceRule.category == normalized_category,
        models.BillingPriceRule.is_active == True,
    ).all()

    matched: List[models.BillingPriceRule] = []
    for rule in candidates:
        rule_stage = _normalize_text(rule.stage)
        rule_provider = _normalize_text(rule.provider)
        rule_model = _normalize_text(rule.model_name)
        rule_resolution = _normalize_resolution(getattr(rule, "resolution", ""))
        if rule.effective_from and rule.effective_from > current_time:
            continue
        if rule.effective_to and rule.effective_to <= current_time:
            continue
        if rule_stage and rule_stage != normalized_stage:
            continue
        if rule_provider and rule_provider != normalized_provider:
            continue
        if rule_model and rule_model != normalized_model:
            continue
        if rule_resolution and rule_resolution != normalized_resolution:
            continue
        matched.append(rule)

    if not matched:
        return None

    def sort_key(rule: models.BillingPriceRule):
        rule_stage = 1 if _normalize_text(rule.stage) else 0
        rule_provider = 1 if _normalize_text(rule.provider) else 0
        rule_model = 1 if _normalize_text(rule.model_name) else 0
        rule_resolution = 1 if _normalize_resolution(getattr(rule, "resolution", "")) else 0
        explicit_count = rule_stage + rule_provider + rule_model + rule_resolution
        return (
            explicit_count,
            rule_stage,
            rule_provider,
            rule_model,
            rule_resolution,
            int(rule.priority or 0),
            int(rule.id or 0),
        )

    return max(matched, key=sort_key)


def create_charge_entry(
    db,
    *,
    user_id: int,
    script_id: int,
    episode_id: int,
    category: str,
    stage: str,
    provider: str,
    model_name: str,
    resolution: str = "",
    quantity: Any,
    billing_key: str,
    operation_key: str,
    initial_status: str = "finalized",
    shot_id: Optional[int] = None,
    storyboard2_shot_id: Optional[int] = None,
    sub_shot_id: Optional[int] = None,
    card_id: Optional[int] = None,
    dashboard_task_log_id: Optional[int] = None,
    attempt_index: int = 1,
    external_task_id: str = "",
    reason: str = "",
    detail_json: str = "",
):
    if not _episode_supports_billing(db, episode_id):
        return None

    existing = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.billing_key == str(billing_key),
    ).first()
    if existing:
        return existing

    rule = resolve_price_rule(
        db,
        category=category,
        stage=stage,
        provider=provider,
        model_name=model_name,
        resolution=resolution,
    )
    if not rule:
        raise ValueError(
            f"No billing rule matched category={category} stage={stage} provider={provider} model={model_name} resolution={resolution}"
        )

    normalized_quantity = _quantize_money(_to_decimal(quantity))
    unit_price = _quantize_money(rule.unit_price_rmb)
    amount = _quantize_money(unit_price * normalized_quantity)
    normalized_resolution = _normalize_resolution(resolution)

    entry = models.BillingLedgerEntry(
        user_id=int(user_id),
        script_id=int(script_id),
        episode_id=int(episode_id),
        shot_id=shot_id,
        storyboard2_shot_id=storyboard2_shot_id,
        sub_shot_id=sub_shot_id,
        card_id=card_id,
        dashboard_task_log_id=dashboard_task_log_id,
        category=_normalize_text(category),
        stage=str(stage or "").strip(),
        provider=str(provider or "").strip(),
        model_name=str(model_name or "").strip(),
        resolution=normalized_resolution,
        billing_mode=str(rule.billing_mode or "").strip(),
        quantity=normalized_quantity,
        unit_price_rmb=unit_price,
        amount_rmb=amount,
        entry_type="charge",
        status=str(initial_status or "finalized").strip(),
        billing_key=str(billing_key),
        operation_key=str(operation_key or ""),
        attempt_index=int(attempt_index or 1),
        external_task_id=str(external_task_id or ""),
        reason=str(reason or ""),
        detail_json=str(detail_json or ""),
    )
    db.add(entry)
    db.flush()
    return entry


def finalize_charge_entry(db, *, billing_key: str):
    charge = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.billing_key == str(billing_key),
        models.BillingLedgerEntry.entry_type == "charge",
    ).first()
    if not charge:
        return None
    if str(charge.status or "").strip() == "reversed":
        return charge
    charge.status = "finalized"
    db.flush()
    return charge


def reverse_charge_entry(db, *, billing_key: str, reason: str):
    charge = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.billing_key == str(billing_key),
        models.BillingLedgerEntry.entry_type == "charge",
    ).first()
    if not charge:
        return None

    existing_refund = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.parent_entry_id == charge.id,
        models.BillingLedgerEntry.entry_type == "refund",
    ).first()
    if existing_refund:
        return None

    refund = models.BillingLedgerEntry(
        user_id=charge.user_id,
        script_id=charge.script_id,
        episode_id=charge.episode_id,
        shot_id=charge.shot_id,
        storyboard2_shot_id=charge.storyboard2_shot_id,
        sub_shot_id=charge.sub_shot_id,
        card_id=charge.card_id,
        dashboard_task_log_id=charge.dashboard_task_log_id,
        category=charge.category,
        stage=charge.stage,
        provider=charge.provider,
        model_name=charge.model_name,
        resolution=getattr(charge, "resolution", "") or "",
        billing_mode=charge.billing_mode,
        quantity=charge.quantity,
        unit_price_rmb=charge.unit_price_rmb,
        amount_rmb=_quantize_money(_to_decimal(charge.amount_rmb) * Decimal("-1")),
        entry_type="refund",
        status="finalized",
        billing_key=f"{billing_key}:refund",
        operation_key=charge.operation_key,
        attempt_index=charge.attempt_index,
        external_task_id=charge.external_task_id,
        reason=str(reason or ""),
        detail_json=charge.detail_json,
        parent_entry_id=charge.id,
    )
    db.add(refund)
    charge.status = "reversed"
    db.flush()
    return refund


def _create_cost_based_text_charge_entry(
    db,
    *,
    context: Optional[Dict[str, Any]],
    stage: str,
    model_name: str,
    cost_rmb: Any,
    billing_key: str,
    operation_key: str,
    external_task_id: str,
    detail_json: str = "",
    shot_id: Optional[int] = None,
    storyboard2_shot_id: Optional[int] = None,
    sub_shot_id: Optional[int] = None,
    card_id: Optional[int] = None,
):
    if not context:
        return None
    if not _episode_supports_billing(db, context.get("episode_id")):
        return None

    existing = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.billing_key == str(billing_key),
    ).first()
    if existing:
        return existing

    normalized_cost = _quantize_money(_to_decimal(cost_rmb))
    quantity = _quantize_money(Decimal("1"))
    entry = models.BillingLedgerEntry(
        user_id=int(context["user_id"]),
        script_id=int(context["script_id"]),
        episode_id=int(context["episode_id"]),
        shot_id=shot_id,
        storyboard2_shot_id=storyboard2_shot_id,
        sub_shot_id=sub_shot_id,
        card_id=card_id,
        category="text",
        stage=str(stage or "").strip(),
        provider="relay",
        model_name=str(model_name or "").strip(),
        resolution="",
        billing_mode="per_call",
        quantity=quantity,
        unit_price_rmb=normalized_cost,
        amount_rmb=normalized_cost,
        entry_type="charge",
        status="finalized",
        billing_key=str(billing_key),
        operation_key=str(operation_key or ""),
        attempt_index=1,
        external_task_id=str(external_task_id or ""),
        reason="",
        detail_json=str(detail_json or ""),
    )
    db.add(entry)
    db.flush()
    return entry


def _create_cost_based_image_charge_entry(
    db,
    *,
    context: Optional[Dict[str, Any]],
    stage: str,
    provider: str,
    model_name: str,
    resolution: str,
    cost_rmb: Any,
    billing_key: str,
    operation_key: str,
    external_task_id: str,
    detail_json: str = "",
    shot_id: Optional[int] = None,
    storyboard2_shot_id: Optional[int] = None,
    sub_shot_id: Optional[int] = None,
    card_id: Optional[int] = None,
):
    if not context or int(context.get("billing_version", 0) or 0) < 1:
        return None

    normalized_cost = _quantize_money(_to_decimal(cost_rmb))
    if normalized_cost <= Decimal("0"):
        return None

    existing = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.billing_key == str(billing_key),
    ).first()
    if existing:
        return existing

    quantity = _quantize_money(Decimal("1"))
    entry = models.BillingLedgerEntry(
        user_id=int(context["user_id"]),
        script_id=int(context["script_id"]),
        episode_id=int(context["episode_id"]),
        shot_id=shot_id,
        storyboard2_shot_id=storyboard2_shot_id,
        sub_shot_id=sub_shot_id,
        card_id=card_id,
        category="image",
        stage=str(stage or "").strip(),
        provider=str(provider or "").strip(),
        model_name=str(model_name or "").strip(),
        resolution=_normalize_resolution(resolution),
        billing_mode="per_call",
        quantity=quantity,
        unit_price_rmb=normalized_cost,
        amount_rmb=normalized_cost,
        entry_type="charge",
        status="finalized",
        billing_key=str(billing_key),
        operation_key=str(operation_key or ""),
        attempt_index=1,
        external_task_id=str(external_task_id or ""),
        reason="",
        detail_json=str(detail_json or ""),
    )
    db.add(entry)
    db.flush()
    return entry


def _query_billing_entries(
    db,
    *,
    user_id: Optional[int] = None,
    script_id: Optional[int] = None,
    episode_id: Optional[int] = None,
    month: Optional[str] = None,
) -> List[models.BillingLedgerEntry]:
    query = db.query(models.BillingLedgerEntry)
    if user_id is not None:
        query = query.filter(models.BillingLedgerEntry.user_id == int(user_id))
    if script_id is not None:
        query = query.filter(models.BillingLedgerEntry.script_id == int(script_id))
    if episode_id is not None:
        query = query.filter(models.BillingLedgerEntry.episode_id == int(episode_id))
    month_range = _get_billing_month_utc_range(month)
    if month_range:
        month_start, month_end = month_range
        query = query.filter(models.BillingLedgerEntry.created_at >= month_start)
        query = query.filter(models.BillingLedgerEntry.created_at < month_end)
    return query.order_by(
        models.BillingLedgerEntry.created_at.asc(),
        models.BillingLedgerEntry.id.asc(),
    ).all()


def _fallback_name(prefix: str, entity_id: int) -> str:
    return f"{prefix} #{int(entity_id)}"


def _parse_ledger_detail(entry: models.BillingLedgerEntry) -> Dict[str, Any]:
    parsed = _parse_detail_json(getattr(entry, "detail_json", ""))
    return parsed if isinstance(parsed, dict) else {}


def _normalize_deleted_label(name: str, *, deleted: bool) -> str:
    value = str(name or "").strip()
    if not value:
        return value
    suffix = "（已删除）"
    if deleted and not value.endswith(suffix):
        return f"{value}{suffix}"
    return value


def _resolve_user_display_name(
    entry: models.BillingLedgerEntry,
    user: Optional[models.User],
) -> Dict[str, Any]:
    detail = _parse_ledger_detail(entry)
    raw_name = str(getattr(user, "username", "") or detail.get("creator_username") or detail.get("username") or "").strip()
    deleted = user is None
    if not raw_name:
        raw_name = _fallback_name("用户", int(entry.user_id))
    return {
        "username": _normalize_deleted_label(raw_name, deleted=deleted and not raw_name.startswith("用户 #")),
        "user_deleted": bool(deleted),
    }


def _resolve_script_display_name(
    entry: models.BillingLedgerEntry,
    script: Optional[models.Script],
) -> Dict[str, Any]:
    detail = _parse_ledger_detail(entry)
    raw_name = str(getattr(script, "name", "") or detail.get("script_name") or detail.get("scriptTitle") or "").strip()
    deleted = script is None
    if not raw_name:
        raw_name = _fallback_name("剧本", int(entry.script_id))
    return {
        "script_name": _normalize_deleted_label(raw_name, deleted=deleted and not raw_name.startswith("剧本 #")),
        "script_deleted": bool(deleted),
    }


def _resolve_episode_display_name(
    entry: models.BillingLedgerEntry,
    episode: Optional[models.Episode],
) -> Dict[str, Any]:
    detail = _parse_ledger_detail(entry)
    raw_name = str(getattr(episode, "name", "") or detail.get("episode_name") or detail.get("episodeTitle") or "").strip()
    deleted = episode is None
    if not raw_name:
        raw_name = _fallback_name("剧集", int(entry.episode_id))
    return {
        "episode_name": _normalize_deleted_label(raw_name, deleted=deleted and not raw_name.startswith("剧集 #")),
        "episode_deleted": bool(deleted),
        "billing_version": int(getattr(episode, "billing_version", 0) or 0),
    }


def _is_test_username(value: Any) -> bool:
    return str(value or "").strip().lower() == "test"


def _should_exclude_billing_user(user: Optional[models.User], entry: models.BillingLedgerEntry) -> bool:
    if user is not None and _is_test_username(getattr(user, "username", "")):
        return True
    detail = _parse_ledger_detail(entry)
    return any(
        _is_test_username(detail.get(field_name))
        for field_name in ("creator_username", "username")
    )


def _ensure_fallback_created_at(row: Dict[str, Any], created_at: Optional[datetime]) -> None:
    if not created_at or bool(row.get("_created_at_locked")):
        return
    current = row.get("_created_at_dt")
    if current is None or created_at < current:
        row["_created_at_dt"] = created_at
        row["created_at"] = _format_datetime(created_at)


def _apply_entry_category_amounts(row: Dict[str, Any], entry: models.BillingLedgerEntry) -> None:
    if str(entry.entry_type or "").strip().lower() == "refund":
        return
    amount = _to_decimal(entry.amount_rmb)
    category = str(entry.category or "").strip().lower()
    if category == "text":
        row["text_amount_rmb"] += amount
    elif category == "image":
        row["image_amount_rmb"] += amount
    elif category == "video":
        row["video_amount_rmb"] += amount


def _build_billing_meta_maps(
    db,
    entries: List[models.BillingLedgerEntry],
) -> Dict[str, Dict[int, Dict[str, Any]]]:
    user_ids = sorted({int(entry.user_id) for entry in entries if entry.user_id is not None})
    script_ids = sorted({int(entry.script_id) for entry in entries if entry.script_id is not None})
    episode_ids = sorted({int(entry.episode_id) for entry in entries if entry.episode_id is not None})

    user_rows = {}
    if user_ids:
        for user in db.query(models.User).filter(models.User.id.in_(user_ids)).all():
            user_rows[int(user.id)] = user

    script_rows = {}
    if script_ids:
        for script in db.query(models.Script).filter(models.Script.id.in_(script_ids)).all():
            script_rows[int(script.id)] = script

    episode_rows = {}
    if episode_ids:
        for episode in db.query(models.Episode).filter(models.Episode.id.in_(episode_ids)).all():
            episode_rows[int(episode.id)] = episode

    user_map: Dict[int, Dict[str, Any]] = {}
    script_map: Dict[int, Dict[str, Any]] = {}
    episode_map: Dict[int, Dict[str, Any]] = {}

    for entry in entries:
        resolved_user_id = int(entry.user_id)
        resolved_script_id = int(entry.script_id)
        resolved_episode_id = int(entry.episode_id)

        user = user_rows.get(resolved_user_id)
        if _should_exclude_billing_user(user, entry):
            continue

        script = script_rows.get(resolved_script_id)
        episode = episode_rows.get(resolved_episode_id)

        user_display = _resolve_user_display_name(entry, user)
        script_display = _resolve_script_display_name(entry, script)
        episode_display = _resolve_episode_display_name(entry, episode)

        if resolved_user_id not in user_map:
            user_created_at = getattr(user, "created_at", None)
            user_map[resolved_user_id] = {
                "user_id": resolved_user_id,
                "username": user_display["username"],
                "user_deleted": bool(user_display["user_deleted"]),
                "created_at": _format_datetime(user_created_at),
                "summary": _build_amount_bucket(),
                "text_amount_rmb": Decimal("0"),
                "image_amount_rmb": Decimal("0"),
                "video_amount_rmb": Decimal("0"),
                "request_count": 0,
                "refund_count": 0,
                "entry_count": 0,
                "_script_ids": set(),
                "_episode_ids": set(),
                "_created_at_dt": user_created_at,
                "_created_at_locked": bool(user_created_at),
            }

        if resolved_script_id not in script_map:
            script_created_at = getattr(script, "created_at", None)
            script_username = user_map[resolved_user_id]["username"]
            if bool(script_display["script_deleted"]):
                detail = _parse_ledger_detail(entry)
                snapshot_username = str(detail.get("creator_username") or detail.get("username") or "").strip()
                if snapshot_username:
                    script_username = _normalize_deleted_label(snapshot_username, deleted=True)
            script_map[resolved_script_id] = {
                "script_id": resolved_script_id,
                "script_name": script_display["script_name"],
                "script_deleted": bool(script_display["script_deleted"]),
                "user_id": resolved_user_id,
                "username": script_username,
                "user_deleted": bool(user_map[resolved_user_id]["user_deleted"]),
                "created_at": _format_datetime(script_created_at),
                "summary": _build_amount_bucket(),
                "text_amount_rmb": Decimal("0"),
                "image_amount_rmb": Decimal("0"),
                "video_amount_rmb": Decimal("0"),
                "request_count": 0,
                "refund_count": 0,
                "entry_count": 0,
                "_episode_ids": set(),
                "_created_at_dt": script_created_at,
                "_created_at_locked": bool(script_created_at),
            }

        if resolved_episode_id not in episode_map:
            episode_created_at = getattr(episode, "created_at", None)
            episode_map[resolved_episode_id] = {
                "episode_id": resolved_episode_id,
                "episode_name": episode_display["episode_name"],
                "episode_deleted": bool(episode_display["episode_deleted"]),
                "script_id": resolved_script_id,
                "script_name": script_map[resolved_script_id]["script_name"],
                "script_deleted": bool(script_map[resolved_script_id]["script_deleted"]),
                "user_id": resolved_user_id,
                "username": user_map[resolved_user_id]["username"],
                "user_deleted": bool(user_map[resolved_user_id]["user_deleted"]),
                "billing_version": int(episode_display["billing_version"]),
                "created_at": _format_datetime(episode_created_at),
                "summary": _build_amount_bucket(),
                "text_amount_rmb": Decimal("0"),
                "image_amount_rmb": Decimal("0"),
                "video_amount_rmb": Decimal("0"),
                "request_count": 0,
                "refund_count": 0,
                "entry_count": 0,
                "_created_at_dt": episode_created_at,
                "_created_at_locked": bool(episode_created_at),
            }

        _ensure_fallback_created_at(user_map[resolved_user_id], entry.created_at)
        _ensure_fallback_created_at(script_map[resolved_script_id], entry.created_at)
        _ensure_fallback_created_at(episode_map[resolved_episode_id], entry.created_at)

    return {
        "users": user_map,
        "scripts": script_map,
        "episodes": episode_map,
    }


def _serialize_episode_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "episode_id": int(row["episode_id"]),
        "episode_name": str(row["episode_name"] or ""),
        "episode_deleted": bool(row.get("episode_deleted", False)),
        "script_id": int(row["script_id"]),
        "script_name": str(row["script_name"] or ""),
        "script_deleted": bool(row.get("script_deleted", False)),
        "user_id": int(row["user_id"]),
        "username": str(row["username"] or ""),
        "user_deleted": bool(row.get("user_deleted", False)),
        "billing_version": int(row.get("billing_version", 0) or 0),
        "created_at": row.get("created_at"),
        "request_count": int(row.get("request_count", 0) or 0),
        "refund_count": int(row.get("refund_count", 0) or 0),
        "entry_count": int(row.get("entry_count", 0) or 0),
        "text_amount_rmb": _format_money(row.get("text_amount_rmb")),
        "image_amount_rmb": _format_money(row.get("image_amount_rmb")),
        "video_amount_rmb": _format_money(row.get("video_amount_rmb")),
        **_serialize_amount_bucket(row["summary"]),
    }


def _serialize_script_row(row: Dict[str, Any], episodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "script_id": int(row["script_id"]),
        "script_name": str(row["script_name"] or ""),
        "script_deleted": bool(row.get("script_deleted", False)),
        "user_id": int(row["user_id"]),
        "username": str(row["username"] or ""),
        "user_deleted": bool(row.get("user_deleted", False)),
        "created_at": row.get("created_at"),
        "episode_count": len(episodes),
        "request_count": int(row.get("request_count", 0) or 0),
        "refund_count": int(row.get("refund_count", 0) or 0),
        "entry_count": int(row.get("entry_count", 0) or 0),
        "text_amount_rmb": _format_money(row.get("text_amount_rmb")),
        "image_amount_rmb": _format_money(row.get("image_amount_rmb")),
        "video_amount_rmb": _format_money(row.get("video_amount_rmb")),
        "episodes": episodes,
        **_serialize_amount_bucket(row["summary"]),
    }


def _serialize_user_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": int(row["user_id"]),
        "username": str(row["username"] or ""),
        "user_deleted": bool(row.get("user_deleted", False)),
        "created_at": row.get("created_at"),
        "script_count": len(row.get("_script_ids") or set()),
        "episode_count": len(row.get("_episode_ids") or set()),
        "request_count": int(row.get("request_count", 0) or 0),
        "refund_count": int(row.get("refund_count", 0) or 0),
        "entry_count": int(row.get("entry_count", 0) or 0),
        "text_amount_rmb": _format_money(row.get("text_amount_rmb")),
        "image_amount_rmb": _format_money(row.get("image_amount_rmb")),
        "video_amount_rmb": _format_money(row.get("video_amount_rmb")),
        **_serialize_amount_bucket(row["summary"]),
    }


def _build_billing_views(
    db,
    *,
    user_id: Optional[int] = None,
    script_id: Optional[int] = None,
    episode_id: Optional[int] = None,
    month: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    entries = _query_billing_entries(
        db,
        user_id=user_id,
        script_id=script_id,
        episode_id=episode_id,
        month=month,
    )
    if not entries:
        return {
            "users": [],
            "scripts": [],
            "episodes": [],
        }

    meta_maps = _build_billing_meta_maps(db, entries)
    user_map = meta_maps["users"]
    script_map = meta_maps["scripts"]
    episode_map = meta_maps["episodes"]

    for entry in entries:
        resolved_user_id = int(entry.user_id)
        resolved_script_id = int(entry.script_id)
        resolved_episode_id = int(entry.episode_id)

        user_row = user_map.get(resolved_user_id)
        script_row = script_map.get(resolved_script_id)
        episode_row = episode_map.get(resolved_episode_id)
        if not user_row or not script_row or not episode_row:
            continue

        for row in (user_row, script_row, episode_row):
            _apply_entry_to_bucket(row["summary"], entry)
            row["entry_count"] += 1
            if str(entry.entry_type or "").strip().lower() == "refund":
                row["refund_count"] += 1
            else:
                row["request_count"] += 1
            _apply_entry_category_amounts(row, entry)

        user_row["_script_ids"].add(resolved_script_id)
        user_row["_episode_ids"].add(resolved_episode_id)
        script_row["_episode_ids"].add(resolved_episode_id)

    serialized_episodes = {
        episode_id: _serialize_episode_row(row)
        for episode_id, row in episode_map.items()
    }

    serialized_scripts: List[Dict[str, Any]] = []
    for row in script_map.values():
        episodes = [
            serialized_episodes[int(episode_id)]
            for episode_id in sorted(
                row.get("_episode_ids") or set(),
                key=lambda value: (
                    str(serialized_episodes[int(value)].get("created_at") or ""),
                    int(value),
                ),
            )
        ]
        serialized_scripts.append(_serialize_script_row(row, episodes))

    serialized_users = [_serialize_user_row(row) for row in user_map.values()]
    serialized_episode_list = list(serialized_episodes.values())

    serialized_scripts.sort(
        key=lambda item: (str(item.get("created_at") or ""), int(item.get("script_id") or 0)),
        reverse=True,
    )
    serialized_episode_list.sort(
        key=lambda item: (str(item.get("created_at") or ""), int(item.get("episode_id") or 0)),
        reverse=True,
    )
    serialized_users.sort(
        key=lambda item: (-_to_decimal(item.get("net_amount_rmb")), int(item.get("user_id") or 0)),
    )

    return {
        "users": serialized_users,
        "scripts": serialized_scripts,
        "episodes": serialized_episode_list,
    }


def get_billing_user_list(db, *, month: Optional[str] = None) -> List[Dict[str, Any]]:
    return _build_billing_views(db, month=month)["users"]


def get_billing_episode_list(
    db,
    *,
    user_id: Optional[int] = None,
    script_id: Optional[int] = None,
    episode_id: Optional[int] = None,
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _build_billing_views(
        db,
        user_id=user_id,
        script_id=script_id,
        episode_id=episode_id,
        month=month,
    )["episodes"]


def get_billing_script_list(
    db,
    *,
    user_id: Optional[int] = None,
    script_id: Optional[int] = None,
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _build_billing_views(
        db,
        user_id=user_id,
        script_id=script_id,
        month=month,
    )["scripts"]


def get_billing_reimbursement_rows(
    db,
    *,
    group_by: str = "script",
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    normalized_group_by = "user" if str(group_by or "").strip().lower() == "user" else "script"
    entries = _query_billing_entries(db, month=month)
    if not entries:
        return []

    meta_maps = _build_billing_meta_maps(db, entries)
    user_map = meta_maps["users"]
    script_map = meta_maps["scripts"]
    buckets: Dict[str, Dict[str, Any]] = {}

    for entry in entries:
        resolved_user_id = int(entry.user_id)
        resolved_script_id = int(entry.script_id)
        month = _format_month_key(entry.created_at)
        if not month:
            continue

        user_row = user_map.get(resolved_user_id)
        if not user_row:
            continue

        if normalized_group_by == "user":
            bucket_key = f"user::{month}::{resolved_user_id}"
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "month": month,
                    "group_by": "user",
                    "user_id": resolved_user_id,
                    "username": str(user_row.get("username") or ""),
                    "amount_rmb": Decimal("0"),
                },
            )
        else:
            script_row = script_map.get(resolved_script_id)
            if not script_row:
                continue
            bucket_key = f"script::{month}::{resolved_script_id}"
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "month": month,
                    "group_by": "script",
                    "script_id": resolved_script_id,
                    "script_name": str(script_row.get("script_name") or ""),
                    "user_id": resolved_user_id,
                    "username": str(user_row.get("username") or ""),
                    "amount_rmb": Decimal("0"),
                },
            )

        bucket["amount_rmb"] += _to_decimal(entry.amount_rmb)

    rows: List[Dict[str, Any]] = []
    for row in buckets.values():
        amount = _to_decimal(row.get("amount_rmb"))
        if amount == Decimal("0"):
            continue
        serialized = {key: value for key, value in row.items() if key != "amount_rmb"}
        serialized["amount_rmb"] = _format_money(amount)
        rows.append(serialized)

    if normalized_group_by == "user":
        rows.sort(
            key=lambda item: (
                str(item.get("month") or ""),
                str(item.get("username") or ""),
                int(item.get("user_id") or 0),
            )
        )
    else:
        rows.sort(
            key=lambda item: (
                str(item.get("month") or ""),
                str(item.get("script_name") or ""),
                int(item.get("script_id") or 0),
            )
        )
    return rows


def get_episode_billing_summary(
    db,
    *,
    user_id: Optional[int] = None,
    script_id: Optional[int] = None,
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for episode_row in get_billing_episode_list(db, user_id=user_id, script_id=script_id, month=month):
        result.append(
            {
                "episode_id": int(episode_row["episode_id"]),
                "user_id": int(episode_row["user_id"]),
                "username": str(episode_row.get("username") or ""),
                "script_id": int(episode_row["script_id"]),
                "script_name": str(episode_row.get("script_name") or ""),
                "text_amount_rmb": episode_row.get("text_amount_rmb", "0.00000"),
                "image_amount_rmb": episode_row.get("image_amount_rmb", "0.00000"),
                "video_amount_rmb": episode_row.get("video_amount_rmb", "0.00000"),
                "refund_amount_rmb": episode_row.get("refund_amount_rmb", "0.00000"),
                "net_amount_rmb": episode_row.get("net_amount_rmb", "0.00000"),
            }
        )
    return result


def _find_script_row(
    db,
    *,
    script_id: int,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for row in get_billing_script_list(db, user_id=user_id, script_id=script_id, month=month):
        if int(row["script_id"]) == int(script_id):
            return row
    return None


def _find_episode_row(
    db,
    *,
    episode_id: int,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for row in get_billing_episode_list(db, user_id=user_id, episode_id=episode_id, month=month):
        if int(row["episode_id"]) == int(episode_id):
            return row
    return None


def _build_billing_detail_breakdowns(entries: List[models.BillingLedgerEntry]) -> Dict[str, Any]:
    overall = _build_amount_bucket()
    category_map: Dict[str, Dict[str, Any]] = {}
    stage_map: Dict[str, Dict[str, Any]] = {}
    model_map: Dict[str, Dict[str, Any]] = {}
    operation_map: Dict[str, Dict[str, Any]] = {}
    serialized_entries: List[Dict[str, Any]] = []

    for entry in entries:
        _apply_entry_to_bucket(overall, entry)
        serialized_entries.append(_serialize_ledger_entry(entry))

        category_key = str(entry.category or "").strip() or "unknown"
        category_bucket = category_map.setdefault(
            category_key,
            {"category": category_key, **_build_amount_bucket()},
        )
        _apply_entry_to_bucket(category_bucket, entry)

        stage_key = str(entry.stage or "").strip() or category_key
        stage_bucket = stage_map.setdefault(
            stage_key,
            {
                "stage": stage_key,
                "category": category_key,
                "provider": str(entry.provider or ""),
                "model_name": str(entry.model_name or ""),
                **_build_amount_bucket(),
            },
        )
        _apply_entry_to_bucket(stage_bucket, entry)

        model_key = f"{str(entry.provider or '').strip()}::{str(entry.model_name or '').strip()}"
        model_bucket = model_map.setdefault(
            model_key,
            {
                "provider": str(entry.provider or ""),
                "model_name": str(entry.model_name or ""),
                "category": category_key,
                **_build_amount_bucket(),
            },
        )
        _apply_entry_to_bucket(model_bucket, entry)

        operation_key = str(entry.operation_key or "").strip() or str(entry.billing_key or "").strip()
        operation_bucket = operation_map.setdefault(
            operation_key,
            {
                "operation_key": operation_key,
                "category": category_key,
                "stage": stage_key,
                "provider": str(entry.provider or ""),
                "model_name": str(entry.model_name or ""),
                "first_created_at": _format_datetime(entry.created_at),
                "last_created_at": _format_datetime(entry.created_at),
                "max_attempt_index": 0,
                **_build_amount_bucket(),
            },
        )
        _apply_entry_to_bucket(operation_bucket, entry)
        operation_bucket["last_created_at"] = _format_datetime(entry.created_at)
        operation_bucket["max_attempt_index"] = max(
            int(operation_bucket["max_attempt_index"]),
            int(entry.attempt_index or 1),
        )

    return {
        "summary": {
            "entry_count": len(serialized_entries),
            "request_count": len([entry for entry in entries if str(entry.entry_type or "").strip().lower() != "refund"]),
            "refund_count": len([entry for entry in entries if str(entry.entry_type or "").strip().lower() == "refund"]),
            **_serialize_amount_bucket(overall),
        },
        "category_summary": [
            {"category": key, **_serialize_amount_bucket(value)}
            for key, value in sorted(
                category_map.items(),
                key=lambda item: (-_to_decimal(item[1]["net_amount_rmb"]), item[0]),
            )
        ],
        "stage_summary": [
            {
                "stage": value["stage"],
                "category": value["category"],
                "provider": value["provider"],
                "model_name": value["model_name"],
                **_serialize_amount_bucket(value),
            }
            for value in sorted(
                stage_map.values(),
                key=lambda item: (-_to_decimal(item["net_amount_rmb"]), str(item["stage"])),
            )
        ],
        "model_summary": [
            {
                "provider": value["provider"],
                "model_name": value["model_name"],
                "category": value["category"],
                **_serialize_amount_bucket(value),
            }
            for value in sorted(
                model_map.values(),
                key=lambda item: (
                    -_to_decimal(item["net_amount_rmb"]),
                    str(item["provider"]),
                    str(item["model_name"]),
                ),
            )
        ],
        "operation_summary": [
            {
                "operation_key": value["operation_key"],
                "category": value["category"],
                "stage": value["stage"],
                "provider": value["provider"],
                "model_name": value["model_name"],
                "first_created_at": value["first_created_at"],
                "last_created_at": value["last_created_at"],
                "max_attempt_index": int(value["max_attempt_index"]),
                **_serialize_amount_bucket(value),
            }
            for value in sorted(
                operation_map.values(),
                key=lambda item: (item["first_created_at"] or "", item["operation_key"]),
            )
        ],
        "entries": serialized_entries,
    }


def get_episode_billing_detail(
    db,
    *,
    episode_id: int,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    episode_row = _find_episode_row(db, episode_id=episode_id, user_id=user_id, month=month)
    if not episode_row:
        return None

    entries = _query_billing_entries(
        db,
        user_id=user_id,
        episode_id=int(episode_id),
        month=month,
    )
    breakdowns = _build_billing_detail_breakdowns(entries)

    return {
        "episode_id": int(episode_row["episode_id"]),
        "episode_name": str(episode_row["episode_name"]),
        "script_id": int(episode_row["script_id"]),
        "script_name": str(episode_row["script_name"]),
        "user_id": int(episode_row["user_id"]),
        "username": str(episode_row["username"]),
        "billing_version": int(episode_row.get("billing_version", 0) or 0),
        "created_at": episode_row.get("created_at"),
        "summary": breakdowns["summary"],
        "category_summary": breakdowns["category_summary"],
        "stage_summary": breakdowns["stage_summary"],
        "model_summary": breakdowns["model_summary"],
        "operation_summary": breakdowns["operation_summary"],
        "entries": breakdowns["entries"],
    }


def get_script_billing_detail(
    db,
    *,
    script_id: int,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    script_row = _find_script_row(db, script_id=script_id, user_id=user_id, month=month)
    if not script_row:
        return None

    entries = _query_billing_entries(
        db,
        user_id=user_id,
        script_id=int(script_id),
        month=month,
    )
    breakdowns = _build_billing_detail_breakdowns(entries)

    return {
        "script_id": int(script_row["script_id"]),
        "script_name": str(script_row["script_name"]),
        "user_id": int(script_row["user_id"]),
        "username": str(script_row["username"]),
        "created_at": script_row.get("created_at"),
        "episode_count": int(script_row.get("episode_count", 0) or 0),
        "episodes": list(script_row.get("episodes") or []),
        "summary": breakdowns["summary"],
        "category_summary": breakdowns["category_summary"],
        "stage_summary": breakdowns["stage_summary"],
        "model_summary": breakdowns["model_summary"],
        "operation_summary": breakdowns["operation_summary"],
        "entries": breakdowns["entries"],
    }




def ensure_deleted_billing_name_snapshots(db, *, script_id: int, username: str, script_name: str) -> int:
    rows = db.query(models.BillingLedgerEntry).filter(
        models.BillingLedgerEntry.script_id == int(script_id)
    ).all()
    updated = 0
    for entry in rows:
        detail = _parse_ledger_detail(entry)
        changed = False
        if str(detail.get("creator_username") or "").strip() != str(username or "").strip():
            detail["creator_username"] = str(username or "").strip()
            changed = True
        if str(detail.get("script_name") or "").strip() != str(script_name or "").strip():
            detail["script_name"] = str(script_name or "").strip()
            changed = True
        if changed:
            entry.detail_json = json.dumps(detail, ensure_ascii=False)
            updated += 1
    if updated:
        db.flush()
    return updated

def _record_text_request_success_from_context(
    *,
    context: Optional[Dict[str, Any]],
    stage: str,
    provider: str,
    model_name: str,
    billing_key: str,
    operation_key: str,
    attempt_index: int = 1,
    detail_json: str = "",
    shot_id: Optional[int] = None,
    card_id: Optional[int] = None,
):
    if not context or int(context.get("billing_version", 0) or 0) < 1:
        return None

    db = SessionLocal()
    try:
        entry = create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="text",
            stage=stage,
            provider=provider,
            model_name=model_name,
            quantity=Decimal("1"),
            billing_key=billing_key,
            operation_key=operation_key,
            shot_id=shot_id,
            card_id=card_id,
            attempt_index=attempt_index,
            detail_json=detail_json,
        )
        db.commit()
        return entry
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_text_request_success(
    *,
    episode_id: int,
    stage: str,
    provider: str,
    model_name: str,
    billing_key: str,
    operation_key: str,
    attempt_index: int = 1,
    detail_json: str = "",
):
    db = SessionLocal()
    try:
        context = get_episode_context(db, episode_id=int(episode_id))
    finally:
        db.close()

    return _record_text_request_success_from_context(
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        billing_key=billing_key,
        operation_key=operation_key,
        attempt_index=attempt_index,
        detail_json=detail_json,
    )


def record_text_request_success_for_card(
    *,
    card_id: int,
    stage: str,
    provider: str,
    model_name: str,
    billing_key: str,
    operation_key: str,
    attempt_index: int = 1,
    detail_json: str = "",
):
    db = SessionLocal()
    try:
        context = get_card_episode_context(db, card_id=int(card_id))
    finally:
        db.close()

    return _record_text_request_success_from_context(
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        billing_key=billing_key,
        operation_key=operation_key,
        attempt_index=attempt_index,
        detail_json=detail_json,
        card_id=int(card_id),
    )


def record_text_request_success_for_shot(
    *,
    shot_id: int,
    stage: str,
    provider: str,
    model_name: str,
    billing_key: str,
    operation_key: str,
    attempt_index: int = 1,
    detail_json: str = "",
):
    db = SessionLocal()
    try:
        context = get_shot_episode_context(db, shot_id=int(shot_id))
    finally:
        db.close()

    return _record_text_request_success_from_context(
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        billing_key=billing_key,
        operation_key=operation_key,
        attempt_index=attempt_index,
        detail_json=detail_json,
        shot_id=int(shot_id),
    )


def record_text_task_cost_for_episode(
    db,
    *,
    episode_id: int,
    stage: str,
    model_name: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_episode_context(db, episode_id=int(episode_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_text_charge_entry(
        db,
        context=context,
        stage=stage,
        model_name=model_name,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
    )


def record_text_task_cost_for_card(
    db,
    *,
    card_id: int,
    stage: str,
    model_name: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_card_episode_context(db, card_id=int(card_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_text_charge_entry(
        db,
        context=context,
        stage=stage,
        model_name=model_name,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
        card_id=int(card_id),
    )


def record_text_task_cost_for_shot(
    db,
    *,
    shot_id: int,
    stage: str,
    model_name: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_shot_episode_context(db, shot_id=int(shot_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_text_charge_entry(
        db,
        context=context,
        stage=stage,
        model_name=model_name,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
        shot_id=int(shot_id),
    )


def record_image_task_cost_for_card(
    db,
    *,
    card_id: int,
    stage: str,
    provider: str,
    model_name: str,
    resolution: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_card_episode_context(db, card_id=int(card_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_image_charge_entry(
        db,
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        resolution=resolution,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
        card_id=int(card_id),
    )


def record_image_task_cost_for_shot(
    db,
    *,
    shot_id: int,
    stage: str,
    provider: str,
    model_name: str,
    resolution: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_shot_episode_context(db, shot_id=int(shot_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_image_charge_entry(
        db,
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        resolution=resolution,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
        shot_id=int(shot_id),
    )


def record_image_task_cost_for_storyboard2_sub_shot(
    db,
    *,
    sub_shot_id: int,
    stage: str,
    provider: str,
    model_name: str,
    resolution: str,
    cost_rmb: Any,
    external_task_id: str,
    billing_key: str,
    operation_key: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = get_storyboard2_sub_shot_context(db, sub_shot_id=int(sub_shot_id))
    detail_json = json.dumps(detail_payload or {}, ensure_ascii=False) if detail_payload is not None else ""
    return _create_cost_based_image_charge_entry(
        db,
        context=context,
        stage=stage,
        provider=provider,
        model_name=model_name,
        resolution=resolution,
        cost_rmb=cost_rmb,
        external_task_id=external_task_id,
        billing_key=billing_key,
        operation_key=operation_key,
        detail_json=detail_json,
        storyboard2_shot_id=int(context["storyboard2_shot_id"]) if context else None,
        sub_shot_id=int(sub_shot_id),
    )
