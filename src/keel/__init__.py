"""keel — project-neutral, multi-agent workflow core.

A fixed *backbone* of ordered steps drives a unit of work (a GitHub issue) from
backlog to done. Projects never fork the backbone; they customise behaviour with:

* **config** (``project.yaml`` — per-project *values*), and
* **extensions** (project-owned *Lego pieces* snapped into named slots, add-only).

The public, deterministic core lives in pure modules (``config``, ``model``,
``extensions``, ``findings``, ``gates``); all network/subprocess I/O is kept in
thin, fail-soft wrappers. See ``docs/proposals/keel-architecture.md``.
"""

__version__ = "1.14.2"
