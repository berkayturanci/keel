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

```markdown
[![Keel Swarm](https://img.shields.io/badge/keel--swarm-DAG%20orchestrated-38bdf8?logo=buffer&logoColor=white)](https://github.com/berkayturanci/keel)
```

---

### 3. Dynamic Coverage Badge (Self-Hosted)
Reflects live branch and line test coverage dynamically updated on every push via GitHub Pages.

```markdown
[![coverage](https://img.shields.io/endpoint?url=https://berkayturanci.github.io/keel/coverage-badge.json)](https://berkayturanci.github.io/keel/coverage/)
```

---

### 4. AI Jury Consensus
Indicates that pull requests and changes are validated by 3-agent multi-vendor AI Jury consensus.

```markdown
[![AI Jury](https://img.shields.io/badge/ai--jury-consensus%20verified-6366f1?logo=scales&logoColor=white)](https://github.com/berkayturanci/ai-jury)
```

---

## Evidence Watermark

When Keel drives an issue through the `s0`–`s12` backbone to completion, the s11 closure step automatically appends an attribution signature and evidence watermark to the PR and issue comment:

```markdown
---
⚓ **Shipped by [keel](https://github.com/berkayturanci/keel)** — *Driven on fixed backbone `s0`→`s12` (with [ai-jury](https://github.com/berkayturanci/ai-jury) consensus)*  
[⭐ Star on GitHub](https://github.com/berkayturanci/keel) · [Add Keel to your repo](https://github.com/berkayturanci/keel#readme)
```

### Opting Out
Projects can customize or disable the watermark signature in their ledger record by passing `watermark: false` or providing a custom signature string.
