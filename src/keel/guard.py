"""Deterministic blocker ruleset — the pure core behind ``keel guard``.

Blocker promotion is what unlocks the night-window bypass at s10 (``keel
merge --hotfix``). Before this module, that promotion was pure agent judgment:
an agent could declare any issue a blocker and merge at 3am. This module makes
the decision a **deterministic, configurable function** of the issue's facts —
its title and labels — so a claimed blocker can be verified against the rule it
allegedly matched.

The matching is pure (no network/subprocess/clock/random and no I/O): given an
issue title, its labels, and a resolved set of :class:`Rule` objects, it returns
the ids of the rules that fired. The CLI gathers the live issue facts and reads
the configured rules; this module only decides.

Rules are resolved from ``policy_pack.blocker_rules`` when present, falling back
to built-in defaults (back-compatible: an absent config yields the defaults).
Each rule is one of two kinds:

* ``label`` — fires when one of the rule's labels is present on the issue
  (case-insensitive exact match).
* ``title-regex`` — fires when the rule's regex matches the issue title.

The built-in defaults cover the heuristics named in the audit (GAP-11):
word-boundary ``\bhotfix\b`` / ``\bsecurity\b`` / ``\bblocker\b`` title regexes
and a configurable blocker label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import config as cfg

GUARD_SCHEMA_VERSION = "keel.guard.v1"

#: Built-in defaults, used when ``policy_pack.blocker_rules`` is absent/empty.
DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {"id": "blocker-label", "kind": "label", "labels": ["blocker"]},
    {"id": "hotfix-label", "kind": "label", "labels": ["hotfix"]},
    {"id": "security-label", "kind": "label", "labels": ["security"]},
    {"id": "blocker-title-regex", "kind": "title-regex",
     "pattern": r"\b(?:hotfix|security|blocker)\b"},
)


class GuardError(ValueError):
    """Raised when a configured blocker rule is malformed."""


@dataclass(frozen=True)
class Rule:
    """A single resolved, immutable blocker rule."""

    id: str
    kind: str  # "label" | "title-regex"
    labels: tuple[str, ...] = ()
    pattern: str | None = None
    _frozenset_labels: frozenset[str] = field(
        init=False, repr=False, compare=False, hash=False, default=frozenset()
    )

    def __post_init__(self):
        if self.kind == "label":
            object.__setattr__(
                self,
                "_frozenset_labels",
                frozenset(want.strip().casefold() for want in self.labels),
            )

    def matches(self, title: str, labels: tuple[str, ...]) -> bool:
        """True if this rule fires for the given issue facts (pure)."""
        if self.kind == "label":
            present = {label.strip().casefold() for label in labels}
            return not self._frozenset_labels.isdisjoint(present)
        # title-regex — ``pattern`` is guaranteed non-empty by :func:`resolve_rules`.
        return re.search(self.pattern or "", title, re.IGNORECASE) is not None


@dataclass(frozen=True)
class GuardResult:
    """The structured outcome of evaluating an issue against the ruleset."""

    title: str
    labels: tuple[str, ...]
    matched: tuple[str, ...]
    rule_ids: tuple[str, ...]

    @property
    def is_blocker(self) -> bool:
        """True when at least one rule fired."""
        return bool(self.matched)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GUARD_SCHEMA_VERSION,
            "title": self.title,
            "labels": list(self.labels),
            "is_blocker": self.is_blocker,
            "matched": list(self.matched),
            "rule_ids": list(self.rule_ids),
        }


def resolve_rules(config: cfg.ProjectConfig | None) -> tuple[Rule, ...]:
    """Resolve the active blocker rules from config, falling back to defaults.

    Reads ``policy_pack.blocker_rules`` (a list of rule dicts). When absent or
    not a list, the built-in :data:`DEFAULT_RULES` are used — keeping projects
    without any blocker config fully back-compatible. Raises :class:`GuardError`
    on a malformed configured rule (fail-closed: a typo must not silently widen
    or narrow the bypass surface).
    """
    raw_rules: Any = None
    if config is not None and isinstance(config.policy_pack, dict):
        raw_rules = config.policy_pack.get("blocker_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        return _build_rules(DEFAULT_RULES, source="defaults")
    return _build_rules(raw_rules, source="policy_pack.blocker_rules")


def _build_rules(raw_rules: Any, *, source: str) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rules):
        where = f"{source}[{index}]"
        if not isinstance(raw, dict):
            raise GuardError(f"{where}: expected an object")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise GuardError(f"{where}: missing non-empty 'id'")
        rule_id = rule_id.strip()
        if rule_id in seen:
            raise GuardError(f"{where}: duplicate rule id {rule_id!r}")
        seen.add(rule_id)
        kind = raw.get("kind")
        if kind == "label":
            labels = raw.get("labels")
            if not isinstance(labels, list) or not labels:
                raise GuardError(f"{where}: label rule needs a non-empty 'labels' list")
            clean = tuple(str(label) for label in labels)
            rules.append(Rule(id=rule_id, kind="label", labels=clean))
        elif kind == "title-regex":
            pattern = raw.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise GuardError(f"{where}: title-regex rule needs a non-empty 'pattern'")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise GuardError(f"{where}: invalid regex {pattern!r}: {exc}") from exc
            rules.append(Rule(id=rule_id, kind="title-regex", pattern=pattern))
        else:
            raise GuardError(f"{where}: unknown rule kind {kind!r}")
    return tuple(rules)


def evaluate(title: str, labels: tuple[str, ...] | list[str], *,
             rules: tuple[Rule, ...]) -> GuardResult:
    """Evaluate the issue facts against ``rules`` (pure).

    Returns a :class:`GuardResult` carrying the ids of every rule that fired
    (in rule order) plus the full set of rule ids considered. Rule ids are
    unique by construction (:func:`resolve_rules` rejects duplicates), so each
    fired rule appears at most once without an explicit dedup step.
    """
    norm_labels = tuple(labels)
    matched = tuple(rule.id for rule in rules if rule.matches(title, norm_labels))
    return GuardResult(
        title=title,
        labels=norm_labels,
        matched=matched,
        rule_ids=tuple(rule.id for rule in rules),
    )


def evaluate_config(title: str, labels: tuple[str, ...] | list[str], *,
                    config: cfg.ProjectConfig | None) -> GuardResult:
    """Convenience: resolve rules from ``config`` then :func:`evaluate`."""
    return evaluate(title, labels, rules=resolve_rules(config))
