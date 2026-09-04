"""The delegate vocabulary — the leaf both halves of keel read it from (#1050).

Two facts have to be known in two places that cannot import each other:

* which providers keel understands with no configuration at all
  (:data:`BUILTIN_DELEGATE_VENDORS`), and
* which reasoning efforts exist and which providers can honour one
  (:data:`EFFORTS`, :func:`supports_effort`).

:mod:`keel.agents` and :mod:`keel.delegate` *dispatch* on them; :mod:`keel.team` and
:mod:`keel.config` *validate against* them. Dispatch imports :mod:`keel.config` for the
config types, and :mod:`keel.config` imports :mod:`keel.team` for the policy type — so
the validating half used to reach the vocabulary through a function-local import.
Benign at run time, but a cyclic-import alert to a scanner (CodeQL 58–61) and a
paragraph of explanation to every reader, every time (#1050).

The fix is not a cleverer import: a vocabulary two sides share is owned by neither.
This module is the leaf that owns it. It imports **nothing** from the rest of the
package, and it has to stay that way — an import added here re-opens the cycle it
exists to close. ``tests/test_vocab_leaf.py`` pins both halves: this module stays a
leaf, and no module reaches these names function-locally again.

Every name keeps its original spelling *and* its original home as a re-export
(``keel.agents.BUILTIN_DELEGATE_VENDORS``, ``keel.delegate.EFFORTS``, …), so this is a
move with no caller, no CLI surface and no ``config_hash`` input changed.
"""

from __future__ import annotations

#: Agent-CLI delegate vendors keel drives as a subprocess. Hardcoded on purpose — not
#: to be confused with the generic ``cli`` *profile* vendor (issue #659), which is the
#: operator-configured escape hatch for every CLI that is not one of these three.
CLI_VENDORS = ("claude", "codex", "agy")

#: Local-model delegate vendors: no agent CLI, no hosted key, and no tools.
LOCAL_VENDORS = ("ollama",)

#: Hosted-API delegate vendors (#548, ``google-api`` added in #666): the vendor's
#: real API keyed by an env token, no agent CLI installed. Same no-tools contract
#: as ``ollama:`` — the
#: orchestrator owns every git/PR step and delegates only code generation. The
#: vendor names match ai-jury's hosted-adapter vocabulary so the value fits the
#: existing first-colon ``vendor:model`` split unchanged.
API_VENDORS = ("anthropic-api", "openai-api", "google-api")

#: Every delegate name keel understands with no configuration at all. Name resolution
#: is **fail-closed**: a ``knobs.delegate_profiles`` entry may not shadow one of these,
#: and the attempt is a config error rather than a silent override (issue #659).
BUILTIN_DELEGATE_VENDORS = CLI_VENDORS + LOCAL_VENDORS + API_VENDORS

#: The one vendor whose endpoint and key-env name come from ``knobs.delegate_profiles``
#: instead of :mod:`keel.api_delegate`'s hardcoded table (#666).
OPENAI_COMPATIBLE = "openai-compatible"

#: Reasoning-effort levels, lowest first. Mapped per vendor by
#: :func:`keel.delegate.plan_run`.
EFFORTS = ("low", "medium", "high")

#: Vendors that have a spelling for reasoning effort. Everything else reaches
#: :func:`keel.delegate._apply_effort`'s fallback, where an ``--effort`` request becomes
#: a warning plus ``effort_applied: false``. That is the right answer for a *run* — a
#: flag that did not take effect must be visible, not fatal — but the wrong one for
#: **config**: a ``knobs.team`` seat that pairs ``claude`` with ``effort: high`` is a
#: policy stating something keel can never do, so :func:`keel.team.team_issues` rejects
#: it up front. The tuple is asserted against ``_apply_effort`` itself by
#: ``tests/test_delegate.py`` so the two cannot drift.
EFFORT_VENDORS = ("agy", "codex", "anthropic-api", "openai-api", "google-api", OPENAI_COMPATIBLE)


def supports_effort(vendor: str) -> bool:
    """True when ``vendor`` can express a reasoning-effort request in its own spelling."""
    return vendor in EFFORT_VENDORS
