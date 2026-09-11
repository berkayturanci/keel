---
schema: keel.learning.v1
title: "capture: the learning document links each changed file, so a graph reader gets the edge"
description: "`render_learning_document` (#1154) records the files a lesson is about in exactly one place: the front-matter list `changed_files:`. That is right for keel's own reader — `retrieve_relevant_learnings`"
repo: keel
pr: 1170
issue: 1166
date: "2026-09-11"
fingerprint: fe7ac00a323e7b6fb54651be5642ed7b8e54b54949521a6626a4404ac823d507
labels:
  - "type:enhancement"
  - "status:backlog"
  - "priority:medium"
  - "role:core"
changed_files:
  - CHANGELOG.md
  - docs/keel/comparison.md
  - docs/keel/configuration.md
  - docs/keel/parameter-reference.md
  - src/keel/capture.py
  - src/keel/cli.py
  - tests/test_capture_learning_sink.py
---

# capture: the learning document links each changed file, so a graph reader gets the edge

`render_learning_document` (#1154) records the files a lesson is about in exactly one place: the front-matter list `changed_files:`. That is right for keel's own reader — `retrieve_relevant_learnings`

## What changed

## Problem

`render_learning_document` (#1154) records the files a lesson is about in exactly one place: the front-matter list `changed_files:`. That is right for keel's own reader — `retrieve_relevant_learnings` scores text, and #1155 will match on the list — and wrong for every **link-following** reader the sink exists to serve. graphify ([Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)), Obsidian, Foam, and the "read the index and follow the links" pattern an LLM uses over a folder of Markdown all build the file ↔ lesson edge from a Markdown link. A bare path inside a YAML list is a string to them.

Measured on a document the sink writes today: the body names no file at all. The three sections are prose, and the paths live only above the `---`. A knowledge graph built over a repository that has been shipping with the sink for a month therefore shows every lesson as an isolated node — connected to nothing, ranked by nothing, and never surfaced beside the file it is about. The retrieval keel promised the sink would enable is exactly the retrieval a graph reader cannot perform on it.

The #1154 contract — *"a directory of Markdown with stable front matter is the entire contract: no vault format, no wikilinks, no plugin API, and keel never learns what reads that directory"* — is the constraint here, not the objection. A relative Markdown link is CommonMark, not a vault format, and keel still does not learn what reads the folder.

## Proposal

The document body gains one fixed section, **Files**, after **What to do differently next time**: one bullet per entry of `changed_files`, each a Markdown link whose text is the repo-relative path.

```markdown
## Files

- [src/keel/capture.py](../../src/keel/capture.py)
- [tests/test_capture_learning_sink.py](../../tests/test_capture_learning_sink.py)
```

Two link forms, chosen by where the sink is:

- **Sink inside the checkout** (a relative `path`, the default `.keel/learning/`): the destination is the path **relative to the document's own directory**, so the link resolves on disk from where the file sits and a graph builder walking the repo makes the edge to a node it already has. The prefix is `os.path.relpath(repo_root, sink_dir)`, rendered with POSIX separators on every platform.
- **Sink outside the checkout** (an absolute or `~` `path`): there is nothing to link to relatively, and a relative link that resolves to nothing is worse than none. The destination is `https://github.com/<owner>/<repo>/blob/<head-sha>/<path>` when `owner`, `repo` and the head sha are known, else the bare path in backticks with no link.

Rules:

- **Front matter is byte-identical to today.** `changed_files:` stays, unchanged, because #1155's exact-match reader and `_front_matter` rely on it. The section is an addition to the body only.
- **The renderer stays pure.** `render_learning_document` takes the link prefix (or the URL base) as an argument; the caller in `keel ship` computes it from the resolved sink directory and `--root`. No path resolution inside the renderer.
- **A path renders as a valid CommonMark destination**: spaces and parentheses are percent-encoded; a path that would still not be a valid destination falls back to backticks.
- **Redaction applies to the section** as to the rest of the document — a path is part of the rendered text, and a `capture_redaction` deny pattern hitting one is scrubbed there too. No separate rule.
- **An empty `changed_files` renders the heading and `_No files recorded._`**, so the document shape is stable (four sections, always) and the reader's summary fallback — the first non-heading line after the title — is untouched, because the new section is last.
- **The section is fed the same list the front matter is fed**: `changed_files` after the #1154 host fallback (the PR's file list when the local diff is empty post-merge), so the two never name different files.

Side effect worth stating: `retrieve_relevant_learnings` scores a file by its own text. The link text puts every path into the body, so a query naming a file now scores the lesson about that file higher than before. That is the behaviour the reader wanted and never had; the changelog entry says so as a measured change, not a surprise.

## Not in this change

- Wikilinks (`[[…]]`), Obsidian aliases, graphify's own JSON, any per-tool front-matter key. CommonMark links only.
- Landing the file on the base branch from a worktree run (#1163) and reading learnings back into briefs (#1155).
- Linking the PR or issue as a document. The numbers are in the front matter; a graph builder that wants those as nodes can make them from `pr:`/`issue:` — that is a reader's choice, not a writer's.

## Acceptance

- A document rendered for the default in-repo sink with `changed_files: [src/keel/capture.py, tests/test_capture.py]` contains a `## Files` section whose two links are `../../src/keel/capture.py` and `../../tests/test_capture.py`, and a test asserts each destination resolves to the file from the document's directory (`os.path.normpath` of the join).
- The same document rendered for a sink at `~/knowledge/…` links `https://github.com/<owner>/<repo>/blob/<sha>/src/keel/capture.py` when owner, repo and head sha are known, and renders the bare path in backticks when any of them is not — never a relative link.
- The front matter of every existing snapshot in `tests/test_capture_learning_sink.py` is unchanged; `_front_matter`, `_learning_title_and_summary` and `retrieve_relevant_learnings` need no change and their tests do not change.
- A path containing a space or parentheses renders as a valid CommonMark link (percent-encoded), asserted by a test that round-trips the destination.
- A redaction deny pattern matching a path is scrubbed in the **Files** section (test).
- An empty `changed_files` renders `## Files` followed by `_No files recorded._`.
- `keel.capture` stays at 100 % line + branch.
- `docs/keel/configuration.md#policy_packcapturelearningsink` documents the fourth section and the two link forms; `CHANGELOG.md` entry names the retrieval-score side effect.

## Docs impact

`docs/keel/configuration.md`, `CHANGELOG.md`.

## Tests

`tests/test_capture_learning_sink.py`, `tests/test_capture.py`, `tests/test_cli.py` (the caller computes the prefix from the resolved sink directory).

## Relates to

- #1154 — the writer this extends.
- #1155 — the reader; unaffected by the front matter, helped by the body.
- #1163 — where the file lands; unaffected.

## What we learned

Gates on the merged head — build: ok, lint: ok, bandit: ok

## What to do differently next time

Recorded automatically from the run. Edit this file to say what the next run should do differently; the read path scores on its text.

## Files

- [CHANGELOG.md](../../CHANGELOG.md)
- [docs/keel/comparison.md](../../docs/keel/comparison.md)
- [docs/keel/configuration.md](../../docs/keel/configuration.md)
- [docs/keel/parameter-reference.md](../../docs/keel/parameter-reference.md)
- [src/keel/capture.py](../../src/keel/capture.py)
- [src/keel/cli.py](../../src/keel/cli.py)
- [tests/test\_capture\_learning\_sink.py](../../tests/test_capture_learning_sink.py)
