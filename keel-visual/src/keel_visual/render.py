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
TITLE_PLACEHOLDER = "__TITLE__"


def render_html(template: str, run_state: dict[str, Any], *, title: str = "keel run") -> str:
    """Return the template with the run-state JSON and title substituted in.

    ``run_state`` is serialised with ``sort_keys`` for deterministic output (two
    identical runs render byte-identical HTML) and with ``<``/``>``/``&`` escaped
    to their JSON ``\\uXXXX`` forms so a string value can never break out of the
    enclosing ``<script>`` (a ``</script>`` in the payload would otherwise close
    the tag). ``title`` is HTML-escaped for safe use in a ``<title>``/heading
    context. Pure — reads only its arguments.
    """
    payload = (
        json.dumps(run_state, sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    safe_title = (
        str(title)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return template.replace(RUN_PLACEHOLDER, payload).replace(TITLE_PLACEHOLDER, safe_title)
