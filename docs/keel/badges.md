# Status Badges & Shields 🛡️

Keel provides dynamic and static SVG status badges that maintainers can embed in their repository `README.md` files, documentation, and PR templates.

## Available Badges

### 1. Keel Backbone Status
Highlights that the project's work units are verified on the fixed 13-step backbone (`s0`–`s12`).

```markdown
[![Keel Backbone](https://img.shields.io/badge/keel-13%20steps%20verified-0d9488?logo=anchor&logoColor=white)](https://github.com/berkayturanci/keel)
```

---

### 2. Keel Swarm Multi-Agent Orchestrator
Highlights that parallel backlog waves are clustered and landed via Keel Swarm DAG orchestration.

> **Swarm is experimental** — a live run opens one pull request per cluster, and nothing in the swarm reviews them ([#1423](https://github.com/berkayturanci/keel/issues/1423)); a live landing has run once, on a sandbox repository, with the reviews done outside the swarm ([#1281](https://github.com/berkayturanci/keel/issues/1281#issuecomment-5935366055)). This badge describes the
> design; do not put it on a repository as a claim that swarm ran there.

```markdown
[![Keel Swarm](https://img.shields.io/badge/keel--swarm-DAG%20orchestrated-38bdf8?logo=buffer&logoColor=white)](https://github.com/berkayturanci/keel)
```

---

### 3. Dynamic Coverage Badge (Self-Hosted)
Reflects live branch and line test coverage dynamically updated on every push via GitHub Pages.

```markdown
[![coverage](https://img.shields.io/endpoint?url=https://keel-ship.dev/coverage-badge.json)](https://keel-ship.dev/coverage/)
```

---

### 4. AI Jury Consensus
Indicates that pull requests are validated by keel's cross-vendor ai-jury review consensus (the
panel's composition and size are set by config, not fixed at three).

```markdown
[![AI Jury](https://img.shields.io/badge/ai--jury-consensus%20verified-6366f1?logo=scales&logoColor=white)](https://github.com/berkayturanci/ai-jury)
```

---

## Evidence Watermark

When Keel drives an issue through the `s0`–`s12` backbone to completion, the s11 closure step automatically appends an attribution signature and evidence watermark to the PR and issue comment:

```markdown
---
⚓ **Shipped by [keel](https://github.com/berkayturanci/keel)** — *Driven on fixed backbone `s0`→`s12`*  
[⭐ Star on GitHub](https://github.com/berkayturanci/keel) · [Add Keel to your repo](https://github.com/berkayturanci/keel#readme)
```

The first line adds ` (with [ai-jury](https://github.com/berkayturanci/ai-jury) consensus)` after
`` `s0`→`s12` `` only when the run's own ledger record says a jury sat: `run_context.jury_mode`
is `gating` or `advisory`, and the recorded panel decision is not `fallback` or `block`. A run
whose `Jury` line reads `off` does not claim ai-jury consensus.

### Opting Out
There is no supported opt-out: no `project.yaml` knob or CLI flag disables or replaces the
watermark. The renderer does honour a `watermark` field on the `ship_run` ledger record
(`false` omits it, a string replaces it), but keel never writes that field, so only a
hand-edited record changes the signature.
