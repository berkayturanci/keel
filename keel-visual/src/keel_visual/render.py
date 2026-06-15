"""Pure HTML rendering: inject a RunState into the visualizer template.

The template is plain HTML/JS/CSS with two placeholders, ``__KEEL_RUN__`` (the
run-state JSON the front-end reads from ``window.KEEL_RUN``) and ``__TITLE__``.
Substitution-only so the front-end's many ``{}`` never collide with Python
format syntax. Pure — no I/O; the CLI loads the template text and writes the
result.
"""

from __future__ import annotations

import json
from typing import Any

RUN_PLACEHOLDER = "__KEEL_RUN__"
BOARD_PLACEHOLDER = "__KEEL_BOARD__"
TITLE_PLACEHOLDER = "__TITLE__"


def _embed(value: Any) -> str:
    """Serialise ``value`` for safe embedding inside a ``<script>`` tag.

    ``sort_keys`` for deterministic output (identical input renders byte-identical
    HTML); ``<``/``>``/``&`` escaped to their JSON ``\\uXXXX`` forms so no string
    value can break out of the enclosing ``<script>`` (a ``</script>`` in the
    payload would otherwise close the tag)."""
    return (
        json.dumps(value, sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _safe_title(title: Any) -> str:
    """HTML-escape ``title`` for safe use in a ``<title>``/heading context."""
    return str(title).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(template: str, run_state: dict[str, Any], *, title: str = "keel run") -> str:
    """Return the run template with the run-state JSON and title substituted in.

    See :func:`_embed` for the script-safe serialisation. Pure — reads only its
    arguments.
    """
    return (template
            .replace(RUN_PLACEHOLDER, _embed(run_state))
            .replace(TITLE_PLACEHOLDER, _safe_title(title)))


def render_board_html(
    template: str, board: list[dict[str, Any]], *, title: str = "keel board",
) -> str:
    """Return the board template with the runs array and title substituted in.

    ``board`` is a list of slim per-run entries (project, label, steps, …) that the
    front-end reads from ``window.KEEL_BOARD``. Same script-safe embedding as
    :func:`render_html`. Pure — reads only its arguments.
    """
    return (template
            .replace(BOARD_PLACEHOLDER, _embed(board))
            .replace(TITLE_PLACEHOLDER, _safe_title(title)))
