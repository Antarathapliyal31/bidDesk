"""Gap-rule loader + matcher.

Loads `app/knowledge/gap_rules.yaml` into strongly-typed Pydantic models
(strict=True, extra=forbid), cross-validates every category / field /
value reference against the products semantic bundle, and exposes:

    load_rules(path, *, products_entity)
    matches_item(rule, item, context_text="")
    fires_for_bid(rule, bid_items)
    evaluate_item_rules(rules, item, context_text)

Item dict contract
------------------
Rules see items as plain dicts. The orchestrator populates them at two
different times (Phase 3 wires this in SPEC §7.4):

  * ITEM-scope rules run at classification time (after
    `classify_items`), so the item carries `category`, parsed attributes
    (nominal_size, length_ft, thickness_in, grade, species, treatment,
    fire_rating, moisture_resistant, r_value, …), and `source_quote`.
    `sale_unit`, `name`, and other post-pricing fields are NOT yet
    populated.

  * BID-scope missing-line rules (underlayment, housewrap, hurricane
    ties) run AFTER pricing on priced lines, because their
    `absent_match` tests `sale_unit` / `name_contains` from the
    matched catalog product.

  * The PT-fastener bid rule can fire either pre- or post-pricing —
    it only needs `category` + `treatment` from the item dict, both of
    which exist at classification time.

Keyword matching
----------------
Word-boundary, case-insensitive, trailing "s" optional, and literal
whitespace flexes to match any run of spaces / tabs / newlines. So
"post" matches "post" / "posts" but not "postpone" / "composite";
"ridge" matches "ridge" / "ridges" but not "fridge"; and "sill plate"
matches "sill plate", "sill  plate" (double space), and the
across-line-break case "sill\\nplate" that pdfplumber can emit.

Matching considers the item's `source_quote` plus a caller-provided
`context_text` (same sentence / section) — never the whole document.

Pass 1 of `evaluate_item_rules` applies fired requirements to an
in-memory copy of the item (using the first entry of `value_in` when
the requirement is multi-valued); Pass 2 evaluates questions against
the updated item. The EMITTED requirement object retains the full
`value_in` — the first-value choice is strictly an evaluation
convenience and never leaks downstream.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.catalog.semantic_layer import Entity
from app.common.yaml_io import YamlLoadError, read_yaml

_HERE = Path(__file__).resolve().parent
_RULES_PATH = _HERE / "gap_rules.yaml"


class GapRulesError(RuntimeError):
    """Raised when gap_rules.yaml fails schema or cross-reference validation."""


# ---------------------------------------------------------------------------
# Typed inner structures
# ---------------------------------------------------------------------------


class _TriggerPattern(BaseModel):
    """Per-item pattern inside a bid-scope rule's triggered_by_any."""

    model_config = ConfigDict(extra="forbid", strict=True)
    category: str
    triggered_when: dict[str, Any] | None = None
    context_keywords: list[str] | None = None
    context_all_of: list[list[str]] | None = None


class _AbsentMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str
    sale_unit: str | None = None
    name_contains: str | None = None


class _AppliesToItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str
    triggered_when: dict[str, Any] | None = None
    context_keywords: list[str] | None = None
    context_all_of: list[list[str]] | None = None

    @model_validator(mode="after")
    def _at_most_one_keyword_style(self) -> _AppliesToItem:
        if self.context_keywords and self.context_all_of:
            raise ValueError(
                "use either context_keywords (OR) or context_all_of (AND-of-OR), not both"
            )
        return self


class _AppliesToBid(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    absent_match: _AbsentMatch | None = None
    triggered_by_any: list[_TriggerPattern] = Field(min_length=1)


class _MissingCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str | None = None
    equals: Any = None
    not_equals: Any = None
    not_in: list[Any] | None = None
    is_null: bool | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> _MissingCondition:
        structured_ops = (self.equals, self.not_equals, self.not_in, self.is_null)
        has_structured = self.field is not None and any(v is not None for v in structured_ops)
        has_description = self.description is not None
        if has_structured == has_description:
            raise ValueError(
                "missing_condition must have EITHER (field + exactly one of "
                "equals/not_equals/not_in/is_null) OR description — not both, not neither"
            )
        if has_structured:
            provided = sum(1 for v in structured_ops if v is not None)
            if provided != 1:
                raise ValueError(
                    "missing_condition: specify exactly one of equals / not_equals / "
                    "not_in / is_null"
                )
        return self


class _Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str
    value: Any = None
    value_in: list[Any] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> _Requirement:
        if (self.value is None) == (self.value_in is None):
            raise ValueError("requirement must have EITHER value OR value_in, not both")
        return self


class GapRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    scope: Literal["item", "bid"]
    action: Literal["requirement", "question"]
    # Parsed once at load from the raw YAML's `applies_to`.
    applies_to_item: _AppliesToItem | None = None
    applies_to_bid: _AppliesToBid | None = None
    missing_condition: _MissingCondition
    requirement: _Requirement | None = None
    question_template: str | None = None
    code_ref: str | None = None
    basis: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> GapRule:
        if (self.applies_to_item is None) == (self.applies_to_bid is None):
            raise ValueError(f"rule {self.id!r}: must parse exactly one applies_to variant")
        if self.scope == "item" and self.applies_to_item is None:
            raise ValueError(f"rule {self.id!r}: scope=item but applies_to_item missing")
        if self.scope == "bid" and self.applies_to_bid is None:
            raise ValueError(f"rule {self.id!r}: scope=bid but applies_to_bid missing")

        if self.action == "requirement":
            if self.requirement is None:
                raise ValueError(
                    f"rule {self.id!r}: action=requirement needs a `requirement:` block"
                )
        else:
            if not self.question_template or not self.question_template.strip():
                raise ValueError(
                    f"rule {self.id!r}: action=question needs a non-empty question_template"
                )

        if self.code_ref is None:
            if not self.basis or "common practice" not in self.basis.lower():
                raise ValueError(
                    f"rule {self.id!r}: code_ref is null; basis must contain 'common practice'"
                )
        else:
            if "(verify)" not in self.code_ref:
                raise ValueError(
                    f"rule {self.id!r}: code_ref {self.code_ref!r} must contain '(verify)' "
                    "until Antara confirms the section against ICC"
                )
        return self


# ---------------------------------------------------------------------------
# Loader + cross-validation
# ---------------------------------------------------------------------------


def _bool_or_int(value: Any) -> set[Any]:
    """For bool fields, treat 0/1 as equivalent to False/True (seed stores 0/1)."""
    return {True, False}


def _allowed_values_for(field_def: Any) -> set[Any] | None:
    """Return the allowed set for a products field, or None if free-form."""
    if field_def.type == "bool":
        return _bool_or_int(None)
    if field_def.type in ("enum", "int") and field_def.values:
        return set(field_def.values)
    return None


def _validate_field_and_value(
    products: Entity,
    rule_id: str,
    where: str,
    field: str,
    value: Any,
) -> None:
    if field not in products.fields:
        raise GapRulesError(
            f"rule {rule_id!r}: {where} references unknown products field {field!r}"
        )
    allowed = _allowed_values_for(products.fields[field])
    if allowed is not None and value not in allowed:
        raise GapRulesError(
            f"rule {rule_id!r}: {where} value {value!r} not in products.{field} "
            f"allowed values {sorted(allowed, key=str)}"
        )


def _validate_item_pattern_fields(
    products: Entity, rule_id: str, where: str, triggered_when: dict[str, Any] | None
) -> None:
    if not triggered_when:
        return
    for field, val in triggered_when.items():
        if field not in products.fields:
            raise GapRulesError(
                f"rule {rule_id!r}: {where}.triggered_when field {field!r} not on products"
            )
        allowed = _allowed_values_for(products.fields[field])
        if allowed is None:
            continue
        values = val if isinstance(val, list) else [val]
        for v in values:
            if v not in allowed:
                raise GapRulesError(
                    f"rule {rule_id!r}: {where}.triggered_when {field}={v!r} not in "
                    f"{sorted(allowed, key=str)}"
                )


def _cross_validate(rule: GapRule, products: Entity) -> None:
    """Fail loudly if a rule references a category, field, or value the
    semantic bundle doesn't declare."""
    categories = set(products.fields["category"].values or [])

    if rule.applies_to_item:
        at = rule.applies_to_item
        if at.category not in categories:
            raise GapRulesError(
                f"rule {rule.id!r}: applies_to.category {at.category!r} not in "
                f"products.category enum {sorted(categories)}"
            )
        _validate_item_pattern_fields(products, rule.id, "applies_to", at.triggered_when)

    if rule.applies_to_bid:
        ab = rule.applies_to_bid
        if ab.absent_match:
            am = ab.absent_match
            if am.category not in categories:
                raise GapRulesError(
                    f"rule {rule.id!r}: absent_match.category {am.category!r} "
                    f"not in products.category enum {sorted(categories)}"
                )
            if am.sale_unit:
                sale_unit_field = products.fields.get("sale_unit")
                sale_units = set(sale_unit_field.values or []) if sale_unit_field else set()
                if sale_units and am.sale_unit not in sale_units:
                    raise GapRulesError(
                        f"rule {rule.id!r}: absent_match.sale_unit {am.sale_unit!r} "
                        f"not in products.sale_unit enum {sorted(sale_units)}"
                    )
        for pattern in ab.triggered_by_any:
            if pattern.category not in categories:
                raise GapRulesError(
                    f"rule {rule.id!r}: triggered_by_any category {pattern.category!r} "
                    f"not in products.category enum {sorted(categories)}"
                )
            _validate_item_pattern_fields(
                products, rule.id, "triggered_by_any", pattern.triggered_when
            )

    mc = rule.missing_condition
    if mc.field:
        if mc.field not in products.fields:
            raise GapRulesError(
                f"rule {rule.id!r}: missing_condition.field {mc.field!r} not on products"
            )
        allowed = _allowed_values_for(products.fields[mc.field])
        if allowed is not None:
            check_values: list[Any] = []
            if mc.equals is not None:
                check_values.append(mc.equals)
            if mc.not_equals is not None:
                check_values.append(mc.not_equals)
            if mc.not_in is not None:
                check_values.extend(mc.not_in)
            for v in check_values:
                if v not in allowed:
                    raise GapRulesError(
                        f"rule {rule.id!r}: missing_condition value {v!r} not in "
                        f"products.{mc.field} allowed {sorted(allowed, key=str)}"
                    )

    if rule.requirement:
        req = rule.requirement
        if req.value is not None:
            _validate_field_and_value(products, rule.id, "requirement", req.field, req.value)
        else:
            for v in req.value_in or []:
                _validate_field_and_value(products, rule.id, "requirement", req.field, v)


def load_rules(path: Path = _RULES_PATH, *, products_entity: Entity) -> list[GapRule]:
    """Parse gap_rules.yaml into GapRule objects.

    `products_entity` is required (keyword-only) — every category / field /
    allowed-value reference is cross-validated against the semantic bundle
    so a typo'd category or field raises `GapRulesError`. There is no
    "skip validation" mode; the orchestrator always has the bundle loaded.
    """
    try:
        raw = read_yaml(path)
    except YamlLoadError as exc:
        raise GapRulesError(str(exc)) from exc
    if "rules" not in raw or not isinstance(raw["rules"], list):
        raise GapRulesError(f"{path.name}: top level must have a `rules:` list")

    out: list[GapRule] = []
    seen_ids: set[str] = set()
    for i, spec in enumerate(raw["rules"]):
        if not isinstance(spec, dict):
            raise GapRulesError(f"{path.name}[{i}]: rule must be a mapping")
        # Split applies_to into the scope-specific variant at load time so
        # the rule object is strongly typed thereafter.
        scope = spec.get("scope")
        applies_to = spec.get("applies_to", {})
        normalized = {k: v for k, v in spec.items() if k != "applies_to"}
        if scope == "item":
            normalized["applies_to_item"] = applies_to
        elif scope == "bid":
            normalized["applies_to_bid"] = applies_to
        try:
            rule = GapRule.model_validate(normalized)
        except ValidationError as exc:
            raise GapRulesError(f"{path.name}[{i}]: {exc}") from exc
        if rule.id in seen_ids:
            raise GapRulesError(f"{path.name}: duplicate rule id {rule.id!r}")
        seen_ids.add(rule.id)
        _cross_validate(rule, products_entity)
        out.append(rule)
    return out


# ---------------------------------------------------------------------------
# Keyword matching (word boundary, optional trailing "s")
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1024)
def _compile_keyword(keyword: str) -> re.Pattern[str]:
    # \b<escaped kw>s?\b lets "plate" match "plate" or "plates" while
    # keeping "post" from matching "postpone" (strict boundaries).
    # re.escape prefixes each whitespace char with "\" in Python 3.7+,
    # so we match (one or more) escaped-whitespace sequences — pattern
    # `(?:\\\s)+` — and replace with `\s+` so matching flexes across
    # single spaces, multiple spaces, tabs, and newlines (pdfplumber
    # emits line breaks mid-phrase).
    escaped = re.escape(keyword)
    flexible = re.sub(r"(?:\\\s)+", r"\\s+", escaped)
    return re.compile(r"\b" + flexible + r"s?\b", re.IGNORECASE)


def _any_keyword_matches(keywords: list[str], haystack: str) -> bool:
    return any(_compile_keyword(kw).search(haystack) for kw in keywords)


def _keyword_context(item: dict[str, Any], context_text: str) -> str:
    return (item.get("source_quote") or "") + " " + (context_text or "")


def _item_keywords_ok(
    applies: _AppliesToItem | _TriggerPattern, item: dict[str, Any], context_text: str
) -> bool:
    haystack = _keyword_context(item, context_text)
    if applies.context_keywords and not _any_keyword_matches(applies.context_keywords, haystack):
        return False
    if applies.context_all_of:
        for group in applies.context_all_of:
            if not _any_keyword_matches(group, haystack):
                return False
    return True


def _item_triggered_when_ok(triggered_when: dict[str, Any] | None, item: dict[str, Any]) -> bool:
    if not triggered_when:
        return True
    for field, allowed in triggered_when.items():
        v = item.get(field)
        if isinstance(allowed, list):
            if v not in allowed:
                return False
        else:
            if v != allowed:
                return False
    return True


# ---------------------------------------------------------------------------
# Item-scope matching
# ---------------------------------------------------------------------------


def _check_missing(cond: _MissingCondition, value: Any) -> bool:
    if cond.description is not None:
        return True  # free-form → treat as "satisfied once applies_to matched"
    if cond.is_null is not None:
        # is_null: true  → "field is null"; is_null: false → "field is populated".
        return (value is None) if cond.is_null else (value is not None)
    if cond.equals is not None:
        return value == cond.equals
    if cond.not_equals is not None:
        return value != cond.not_equals
    if cond.not_in is not None:
        return value not in cond.not_in
    return False


def matches_item(rule: GapRule, item: dict[str, Any], context_text: str = "") -> bool:
    """Does an item-scope `rule` fire for this item?"""
    if rule.scope != "item" or rule.applies_to_item is None:
        return False
    at = rule.applies_to_item
    if item.get("category") != at.category:
        return False
    if not _item_triggered_when_ok(at.triggered_when, item):
        return False
    if not _item_keywords_ok(at, item, context_text):
        return False
    field = rule.missing_condition.field
    return _check_missing(rule.missing_condition, item.get(field) if field else None)


# ---------------------------------------------------------------------------
# Bid-scope matching
# ---------------------------------------------------------------------------


def _absent_match_has_hit(absent: _AbsentMatch, bid_items: list[dict[str, Any]]) -> bool:
    """Return True if ANY item in the bid matches the absent_match pattern
    (meaning the bid already has the thing → rule stays silent)."""
    for it in bid_items:
        if it.get("category") != absent.category:
            continue
        if absent.sale_unit and it.get("sale_unit") != absent.sale_unit:
            continue
        if absent.name_contains:
            name = (it.get("name") or "").lower()
            if absent.name_contains.lower() not in name:
                continue
        return True
    return False


def _bid_item_matches_pattern(pattern: _TriggerPattern, item: dict[str, Any]) -> bool:
    if item.get("category") != pattern.category:
        return False
    if not _item_triggered_when_ok(pattern.triggered_when, item):
        return False
    return _item_keywords_ok(pattern, item, item.get("context_text", "") or "")


def fires_for_bid(rule: GapRule, bid_items: list[dict[str, Any]]) -> bool:
    """Does a bid-scope `rule` fire for this collection of items?"""
    if rule.scope != "bid" or rule.applies_to_bid is None:
        return False
    ab = rule.applies_to_bid
    if ab.absent_match is not None and _absent_match_has_hit(ab.absent_match, bid_items):
        return False
    for pattern in ab.triggered_by_any:
        for it in bid_items:
            if _bid_item_matches_pattern(pattern, it):
                return True
    return False


# ---------------------------------------------------------------------------
# End-to-end evaluation (Pass 1 requirements → Pass 2 questions)
# ---------------------------------------------------------------------------


def evaluate_item_rules(
    rules: list[GapRule], item: dict[str, Any], context_text: str = ""
) -> tuple[list[GapRule], list[GapRule]]:
    """Return `(fired_requirements, fired_questions)` for this item.

    Requirement rules are evaluated first; each fired requirement is
    applied to an in-memory copy of the item (for a `value_in`
    requirement, the first entry is used as a deterministic evaluation
    choice). Pass 2 evaluates question rules against the updated copy.
    The EMITTED requirement rule still carries the full `value_in` — the
    first-value choice never leaves this function.
    """
    item_mut = dict(item)
    reqs: list[GapRule] = []
    for rule in rules:
        if (
            rule.scope == "item"
            and rule.action == "requirement"
            and matches_item(rule, item_mut, context_text)
        ):
            reqs.append(rule)
            req = rule.requirement
            if req is None:
                # Load-time schema already enforces this invariant; the
                # runtime check is defensive so `python -O` (which drops
                # `assert`) can't produce a silently-broken evaluation.
                raise GapRulesError(
                    f"rule {rule.id!r}: action=requirement without requirement block"
                )
            if req.value is not None:
                item_mut[req.field] = req.value
            elif req.value_in:
                item_mut[req.field] = req.value_in[0]
    questions: list[GapRule] = []
    for rule in rules:
        if (
            rule.scope == "item"
            and rule.action == "question"
            and matches_item(rule, item_mut, context_text)
        ):
            questions.append(rule)
    return reqs, questions
