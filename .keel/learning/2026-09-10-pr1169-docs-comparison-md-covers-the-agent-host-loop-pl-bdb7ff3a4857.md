---
schema: keel.learning.v1
title: "docs: comparison.md covers the agent-host loop plugins — Ralph loop, compound-engineering, graphify"
description: "`docs/keel/comparison.md` (#151, June 2026) positions keel against merge queues, AI PR reviewers, issue-to-PR agents, policy-as-code gates and orchestration frameworks. It does not mention the categor"
repo: keel
pr: 1169
issue: 1167
date: "2026-09-10"
fingerprint: bdb7ff3a4857ec29ebc91ab272194fd0ad13edbec62659c14064c4fcf0ec1bd0
labels:
  - documentation
  - "type:enhancement"
  - "status:backlog"
  - "priority:medium"
  - "role:core"
changed_files:
  - CHANGELOG.md
  - docs/keel/comparison.md
---

# docs: comparison.md covers the agent-host loop plugins — Ralph loop, compound-engineering, graphify

`docs/keel/comparison.md` (#151, June 2026) positions keel against merge queues, AI PR reviewers, issue-to-PR agents, policy-as-code gates and orchestration frameworks. It does not mention the categor

## What changed

## Problem

`docs/keel/comparison.md` (#151, June 2026) positions keel against merge queues, AI PR reviewers, issue-to-PR agents, policy-as-code gates and orchestration frameworks. It does not mention the category a keel operator is most likely to be asked about today: the **agent-host workflow plugins** that ship a loop of their own.

- **Ralph loop** — Anthropic's `/ralph-loop` plugin ([claude.com/plugins/ralph-loop](https://claude.com/plugins/ralph-loop), [`anthropics/claude-code` `plugins/ralph-wiggum`](https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/README.md)): a stop hook re-feeds the same prompt until the agent emits a completion promise.
- **compound-engineering plugin** — Every's plan → work → review → compound workflow ([EveryInc/compound-engineering-plugin](https://github.com/everyinc/compound-engineering-plugin)), 21k+ stars, near-daily releases, now shipped for Claude Code, Codex and Cursor.
- **graphify** — a `/graphify` skill that turns a repository plus its docs into a queryable knowledge graph ([Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)).

Two of the three overlap with things keel already has a *name* for: `--compound` is literally the compound-engineering profile (`ship.md`, `parity-matrix.md`), and `compound-learning` is the capture marker. The document that positions keel says nothing about either, so "is keel a competitor to X" has been answered in chat and never in the repository. The third is not a competitor at all — it is a reader of the learning directory keel writes (#1154) — and that relationship is written down nowhere.

## Proposal

A **Category 5 — Agent-host workflow plugins and autonomous loops** section in `docs/keel/comparison.md`, in the shape the existing categories use (what it is · license / model · what keel does that it does not · what it does that keel does not · idea to borrow), with facts cited inline and interpretation labelled **Assessment** as the rest of the document does.

### Ralph loop

- **What**: a Claude Code plugin; the stop hook blocks session exit and re-feeds the original prompt until the completion promise appears; the prompt is fixed, the codebase and test output change between iterations; `/cancel-ralph` stops it.
- **Position (Assessment)**: a *single-step iteration strategy*, host-specific, with an **LLM-declared** completion criterion. Not a backbone and not a competitor: it has no notion of issue, review, merge window, lock, attribution or record. It is what an s4 implementer may do inside its turn.
- **What keel does that it does not**: everything from s5 on, and the record. keel's counterpart is the s4 iteration loop in #1165 — the same fixed-brief iteration with the **gates** as the judge, bounded by `max_iterations`, recorded per iteration in the ledger, on every host keel runs in.
- **Idea to borrow**: "the prompt stays fixed and the evidence changes" as the shape of the iteration brief — adopted by #1165.

### compound-engineering plugin

- **What**: a skill-and-agent library (dozens of skills and agents) around a `plan → work → review → compound` loop, plus a lessons file the agent reads on every future session.
- **Position (Assessment)**: the closest overlap in the whole document. keel's `--compound` profile models its four-step shape as `workflow_profile.step_overrides` on s4/s7/s9/s11, and its per-task lessons file is what `policy_pack.capture.learning.sink` writes. The difference is the layer: a prompt library with no deterministic core, no merge invariants, no config hash, no ledger; keel is the core with host adapters. keel does not require it and can run it *as* the compound helper — `ship.md` already says compound helpers "may be supplied by the host runtime".
- **What it does that keel does not**: the brainstorm / grill / deepen-plan front half and the breadth of the skill library. keel starts at a ready issue.
- **Idea to borrow**: none for core; its "lessons the agent reads next session" is #1155.

### graphify

- **What**: local deterministic AST parsing plus LLM concept extraction over code, Markdown, PDFs and images; outputs an interactive HTML graph, GraphRAG JSON and a plain-language report; for Claude Code, Cursor, Codex and Gemini CLI.
- **Position (Assessment)**: not a workflow, so not a competitor — a **reader** of the directory keel's sink writes. State the contract explicitly: `.keel/learning/*.md` with `keel.learning.v1` front matter is plain Markdown a graph builder ingests as-is, with `labels` clustering lessons and `changed_files` naming the code nodes; keel never learns what reads it (#1154). Say what makes the edge explicit: #1166 renders `changed_files` as relative Markdown links, which a link-following builder resolves; and say what still blocks it on a fresh clone: #1163 (the file does not yet land on the base branch from a worktree run).
- **Idea to borrow**: none for core.

### Elsewhere in the document

- One row each in the **Comparison table**: Ralph (agent-agnostic ❌, everything else ❌, OSS), compound-engineering (agent-agnostic ◑ — Claude Code / Codex / Cursor; AI review ◑ — review skills; project config ◑; OSS), graphify (a reader, not a workflow — mark the workflow columns ❌ and add a footnote rather than pretend it competes).
- One row each in **Borrowed ideas mapped to the roadmap**, naming #1165, #1155 and #1166.
- One paragraph in the **Positioning statement**: keel is not a competitor to a host-plugin loop; it is the layer that decides *when the loop is done* (the gates) and *what happens after* (review, merge, capture), and can host any of these as an s4 implementer or a learning reader.
- The **Sources** list gains every URL cited above.
- A date line on the new section ("added September 2026"), because the rest of the document carries June 2026 and the two research passes should not read as one.

## Not in this change

- Any code change. Docs only; the diff is entirely under `docs/**`, so this is tier 1.
- Re-benchmarking the categories already in the document. The June 2026 research stands.
- The README's integration catalog (`website/`), which lists "Compound" as a skill library already and is generated separately.

## Acceptance

- `docs/keel/comparison.md` has a Category 5 section covering the three tools in the five-part shape the other categories use, with **Assessment** labels on interpretation.
- The comparison table, the borrowed-ideas table, the positioning statement and the sources list each reference all three.
- Every factual claim about a third-party tool carries an inline link to that tool's own README, plugin page or repository.
- The section names #1165, #1166, #1155 and #1163 where the text above says it does, so a reader can follow the roadmap from the comparison.
- `make lint` passes and every test that reads `docs/keel/*.md` (`tests/test_docs_claims.py` and neighbours) still passes.
- `CHANGELOG.md` entry under the unreleased heading.

## Docs impact

`docs/keel/comparison.md`, `CHANGELOG.md`.

## Tests

No new tests; the existing docs tests must stay green.

## Relates to

- #151 — the original comparison.
- #1165 — the s4 iteration loop this section points at.
- #1166 — the learning links a graph reader needs.
- #1154, #1155, #1163 — the learning sink, its reader, and where the file lands.

## What we learned

Gates on the merged head — build: ok, lint: ok, bandit: ok

## What to do differently next time

Recorded automatically from the run. Edit this file to say what the next run should do differently; the read path scores on its text.
