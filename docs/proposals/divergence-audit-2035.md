# Cross-repo divergence audit — smartinventory ↔ ai-infra ↔ ingreview

> Tracking issue: **smartinventory#2035** — _modularise `/ship` & portable commands
> into ai-infra (thin-consumer, config-driven, tested); kill the sync-overwrite._
> Phase 1 (cross-repo audit). **Read-only audit — no files were synced, overwritten,
> or edited.** This document is the deliverable.

## 0. TL;DR

- **ai-infra is not a project-neutral canon — it is a smartinventory mirror.** The
  "portable" command bodies hardcode Android/Kotlin/Realm/gradle paths, the `develop`
  base branch, and the `Etc/GMT-3` (UTC+3) merge window. `ship.md` alone carries 15
  Android references, 10 UTC+3 references, and 8 `develop` references.
- **ingreview has been partially steam-rolled.** Six command bodies in
  `ingreview/.claude/commands/` are leaked smartinventory Android copies (`ship`,
  `ship-v2`, `implement`, `pr-loop`, `regression`, `overnight`). `regression.md` is
  **byte-identical** to smartinventory's Android version. They reference an
  `android-developer` agent **that does not exist in ingreview** (a Dart/Flutter repo).
- **ingreview also has genuine, clean Flutter adaptations that a sync would destroy**
  (`coverage`, `flake-audit`, `ui-test`, `wrap`, `deps-audit`, `post-issue-1-regression`).
  These are exactly the "project-specific bits" the overwrite-sync puts at risk.
- **A leaked top-level mirror exists in ingreview.** `ingreview/commands/*.md` and
  `ingreview/agents/*.md` (14 tracked files) are verbatim copies of **ai-infra** pushed
  to the wrong path (repo root instead of `.claude/`). They are orphan duplicates that
  Claude Code never reads and that diverge from the real `.claude/` files.
- **`post-issue-1-regression` is mislabeled.** It is Tier-C (project-only) per #2035, yet
  it physically lives in `ai-infra/commands/` and was synced downstream. It should never
  have been in ai-infra.
- **The project-config schema is too thin** to support config-driven commands. It is
  missing `timezone`, `merge_window`, `tier3_globs`, `docs_gate_paths`,
  `docs_only_allowlist`, `ci_workflows`, `sot_doc`, and a role→agent `implementer_agents`
  map (the knobs #2035 enumerates).

---

## 1. Inventory

### 1.1 ai-infra layout

```
ai-infra/
├── README.md
├── agents/                 code-reviewer.md, tester.md            (2 — Tier-A portable)
├── commands/               19 .md  (see §3 table)
├── docs/                   agy-settings-template.json,
│                           claude-code-global-setup.md, codex-config-template.toml,
│                           compound-learning-spec.md, parallel-agents.md
├── gemini-agents/          49 × ce-*.md   (CE persona reviewers)
├── hooks/                  session-start.sh
├── projects/               ingreview.json, smartinventory.json
└── scripts/                compound-learning.sh, sync.sh
```

**`commands/` (19):** `ci-check`, `coverage`, `deps-audit`, `flake-audit`, `implement`,
`morning`, `overnight`, `post-issue-1-regression`, `pr-loop`, `regression`,
`review-all-day`, `review-cycle-to-pr`, `ship`, `ship-v2`, `stale-prs`,
`sync-to-ai-infra`, `triage`, `ui-test`, `wrap`.
(Note: `android-build`/`web-check` correctly absent; `post-issue-1-regression` present —
**defect**, it is Tier-C project-only.)

#### projects/*.json — current keys (dumped)

Both configs share the **same 9 keys**:
`build_lint`, `build_test`, `default_branch`, `owner`, `platform`, `platform_agents`,
`repo`, `skip_commands`, `substitutions`.

| key | `smartinventory.json` | `ingreview.json` |
|---|---|---|
| `owner` | `berkayturanci` | `berkayturanci` |
| `repo` | `smartinventory` | `ingreview` |
| `default_branch` | `develop` | `main` |
| `platform` | `android-web` | `flutter-supabase` |
| `build_test` | `cd android && ./gradlew test --no-daemon …` | `cd apps/mobile && flutter test …` |
| `build_lint` | `cd android && ./gradlew lint --no-daemon …` | `cd apps/mobile && flutter analyze …` |
| `platform_agents` | `["android-developer","web-developer"]` | `["flutter-developer","supabase-developer"]` |
| `substitutions` | `{REPO, OWNER, DEFAULT_BRANCH}` | `{REPO, OWNER, DEFAULT_BRANCH}` |
| `skip_commands` | `["android-build","web-check"]` | `[]` |

**Gap:** the schema captures *build commands, branch, agents* but **none** of the
behavioural knobs the command bodies actually hardcode (timezone/merge-window,
tier-3 globs, docs gates, CI workflow names, SoT doc). See §5.2.

> Note: the config supports a `substitutions` map + `build_test`/`build_lint`, but **no
> command body actually reads them** — the bodies inline `./gradlew …`, `develop`,
> `Etc/GMT-3`, and `android-developer` directly. The config exists but is not wired in.

### 1.2 ingreview `.claude/` tree (Dart/Flutter, default branch `main`)

```
ingreview/.claude/
├── agents/      code-reviewer.md, flutter-developer.md, supabase-developer.md, tester.md
├── commands/    19 .md  (Tier-A + Tier-B + post-issue-1-regression; NO android-build/web-check)
├── hooks/       session-start.sh
├── priorities.md, sessions.md, settings.json
```

- **Agents are correct for the platform**: `flutter-developer`, `supabase-developer`
  (no `android-developer`/`web-developer`). This is what makes the leaked `ship`/
  `implement` bodies **broken**: they dispatch to `android-developer`/`web-developer`,
  which do not exist here.
- **`settings.json` is correctly project-scoped** (`Bash(flutter:*)`, `Bash(dart:*)`,
  `Bash(fvm:*)`, `Bash(supabase:*)`). Project knobs *are* maintained here — but the
  command bodies don't consume them.
- **Leaked top-level mirror (defect):** `ingreview/commands/` (12 files) and
  `ingreview/agents/` (2 files) are **verbatim copies of ai-infra** at the wrong path.
  They are byte-identical to ai-infra and **differ** from the real `.claude/` copies —
  orphan duplicates created by the sync writing to ai-infra-relative paths
  (`commands/x.md`) instead of `.claude/`-prefixed paths in the downstream repo.

---

## 2. Verdict semantics

| Verdict | Meaning |
|---|---|
| **IDENTICAL (portable)** | Byte-identical across all three **and** contains no project specifics — genuinely portable. |
| **IDENTICAL (overwritten)** | Byte-identical across all three **but** full of one project's specifics — identical *because* the sync flattened the others. |
| **DRIFTED (adapted)** | ingreview differs because it holds a **legitimate Flutter adaptation** — must be **preserved**; a sync would destroy it. |
| **DRIFTED (stale)** | Differs due to independent edits (e.g. smartinventory evolved past ai-infra); no project semantics at stake. |
| **OVERWRITTEN (leaked)** | ingreview's body is smartinventory's Android content (verbatim or near) — the project's own version was lost and must be **recovered**. |

---

## 3. Three-way diff — Tier-A & Tier-B

`SI` = `smartinventory/.claude/commands` · `AI` = `ai-infra/commands` ·
`IN` = `ingreview/.claude/commands`. Checksum columns are the first 8 hex of md5.

| Cmd | Tier | SI | AI | IN | SI↔AI | AI↔IN | ingreview verdict |
|---|---|---|---|---|---|---|---|
> ⤴ = smartinventory value advanced **after** the first audit snapshot. `ship`/`ship-v2`
> moved again in **#2037** (`6da6190`, attribution/vendor+model labels, issue #2036):
> `ship` `77cabcd2`→`e4cfbbad`, `ship-v2` `233fdad0`→`d680d1be`. ai-infra (`9f97ec61`/
> `786ec0b7`) and ingreview were **not** updated — the gap to the stale "canon" only widened,
> and the SI body now carries **more** hardcodes (ship: 18 Android / 11 UTC+3 / 40 `develop`).
> Every other command's SI checksum is unchanged from the first snapshot.

| `ci-check` | A | `469c283b` | `469c283b` | `469c283b` | = | = | **IDENTICAL (portable)** ✅ |
| `sync-to-ai-infra` | A | `94368f64` | `94368f64` | `94368f64` | = | = | **IDENTICAL (portable)** ✅ |
| `pr-loop` | A | `beeaa23b` | `28847512` | `28847512` | ≠ | = | **OVERWRITTEN (leaked)** — Android (5 refs) |
| `wrap` | A | `e06ac0ce` | `e06ac0ce` | `4a1ba351` | = | ≠ | **DRIFTED (adapted)** ✅ Flutter, clean |
| `stale-prs` | A | `efbed486` | `efbed486` | `1ae5e350` | = | ≠ | **DRIFTED (adapted)** ⚠ UTC+3 leak |
| `triage` | A | `b5e846e7` | `b5e846e7` | `5129d4cf` | = | ≠ | **DRIFTED (adapted)** ✅ clean |
| `flake-audit` | A | `b04d309a` | `b04d309a` | `74122069` | = | ≠ | **DRIFTED (adapted)** ✅ Flutter, clean |
| `review-cycle-to-pr` | A | `87f3b2dc` | `a9fe44f6` | `4cac0051` | ≠ | ≠ | **DRIFTED (adapted)** ✅ Flutter-ish |
| `morning` | A | `81d00248` | `5172841c` | `00d7a645` | ≠ | ≠ | **DRIFTED (adapted)** ⚠ UTC+3 leak (×3) |
| `overnight` | A | `54c59c3d` | `54c59c3d` | `55355e59` | = | ≠ | **OVERWRITTEN (leaked)** — Kotlin + UTC+3 |
| `ship` | B | `e4cfbbad` ⤴ | `9f97ec61` | `084fb3a7` | ≠ | ≠ | **OVERWRITTEN (leaked)** — IN: 17 Android, 10 UTC+3 |
| `ship-v2` | B | `d680d1be` ⤴ | `786ec0b7` | `cb8e07d2` | ≠ | ≠ | **OVERWRITTEN (leaked)** — IN: Realm/UTC+3 |
| `implement` | B | `8af3827e` | `c94d3c85` | `c94d3c85` | ≠ | = | **OVERWRITTEN (leaked)** — 12 Android |
| `regression` | B | `650498aa` | `650498aa` | `650498aa` | = | = | **IDENTICAL (overwritten)** — Kotlin/Realm verbatim |
| `review-all-day` | B | `778c0b80` | `778c0b80` | `83cad568` | = | ≠ | **DRIFTED (adapted)** ⚠ UTC+3 leak (×4) |
| `deps-audit` | B | `6abaad68` | `6abaad68` | `5d051765` | = | ≠ | **DRIFTED (adapted)** ⚠ UTC+3 leak (×2) |
| `coverage` | B | `b801e2c5` | `b801e2c5` | `5e6bf918` | = | ≠ | **DRIFTED (adapted)** ✅ Flutter, clean |
| `ui-test` | B | `411aef35` | `411aef35` | `98397ba3` | = | ≠ | **DRIFTED (adapted)** ✅ Flutter, clean |

**Tier-C (for completeness):**

| Cmd | SI | AI | IN | Note |
|---|---|---|---|---|
| `post-issue-1-regression` | `635dee16` | `fdb5d241` | `ef56411e` | **Mislabeled** — Tier-C but present in ai-infra (Android) & ingreview (Flutter). |
| `android-build` | `a736c8f4` | — | — | Correct: SI-only. |
| `web-check` | `c16a5661` | — | — | Correct: SI-only. |

### 3.1 ingreview contamination matrix (token counts)

Counts of Android-specific tokens (`kotlin|realm|gradle|paparazzi|android-developer|web-developer|.aab|espresso`), Flutter-adaptation tokens (`flutter|dart|supabase|pubspec|deno`), and UTC+3 (`Etc/GMT-3`) in `ingreview/.claude/commands/`:

| Cmd | Android leak | Flutter adapt | UTC+3 leak | Reading |
|---|---:|---:|---:|---|
| `implement` | 12 | 0 | 0 | pure leaked Android copy |
| `ship` | 17 | 0 | 10 | pure leaked Android copy (worst) |
| `ship-v2` | 4 | 0 | 1 | leaked Android copy |
| `regression` | 9 | 0 | 0 | leaked (byte-identical to SI) |
| `pr-loop` | 5 | 0 | 0 | leaked Android copy |
| `overnight` | 3 | 0 | 4 | leaked Android + TZ |
| `morning` | 0 | 5 | 3 | adapted, TZ leak remains |
| `review-all-day` | 0 | 2 | 4 | adapted, TZ leak remains |
| `deps-audit` | 0 | 31 | 2 | adapted, TZ leak remains |
| `stale-prs` | 0 | 0 | 1 | TZ leak only |
| `coverage` | 0 | 25 | 0 | clean Flutter ✅ |
| `flake-audit` | 0 | 10 | 0 | clean Flutter ✅ |
| `ui-test` | 0 | 9 | 0 | clean Flutter ✅ |
| `wrap` | 0 | 6 | 0 | clean Flutter ✅ |
| `triage` | 0 | 1 | 0 | clean ✅ |
| `review-cycle-to-pr` | 0 | 2 | 0 | clean ✅ |
| `ci-check` | 0 | 0 | 0 | neutral portable ✅ |
| `sync-to-ai-infra` | 0 | 0 | 0 | neutral portable ✅ |
| `post-issue-1-regression` | 0 | 15 | 0 | clean Flutter (but Tier-C) ✅ |

### 3.2 Exact leaked lines (representative, ingreview)

**`ingreview/.claude/commands/ship.md`** — wrong branch + nonexistent agents + smartinventory paths:

```
3:   allowed-tools: … Bash(./gradlew:*) …                 ← gradle (no gradle in a Flutter repo)
102: # No delegation label → default Claude subagent (android-developer / web-developer)
173: CONCL=$(gh run list --branch develop …)              ← ingreview's base branch is `main`
264: Pick `android-developer` or `web-developer` …        ← neither agent exists in ingreview
272: create a git worktree off `origin/develop` …         ← should be origin/main
276: deprecated sibling form (`../smartinventory-<N>`) …  ← smartinventory path literal
373: android/smartinventory/src/main/.*/object/db/ (Realm models)
377: android/smartinventory/src/main/kotlin/.*/lifecycle/ or ListFragment.kt
```

**`ingreview/.claude/commands/regression.md`** — `diff` confirms **byte-identical** to
`smartinventory/.claude/commands/regression.md`; e.g. line 124
`| android | android/ | Kotlin/Java logic, Realm, billing, lifecycle … |`.

**`ingreview/.claude/commands/deps-audit.md`** — adapted to Flutter, but TZ leaked:

```
36: Compute the run date in the project timezone (UTC+3):
39: DATE=$(TZ='Etc/GMT-3' date +%Y-%m-%d)
```

---

## 4. What got overwritten / must be recovered (per repo)

### 4.1 ingreview — recover (project content lost to a past sync)

These bodies were replaced wholesale by smartinventory's Android content. ingreview's
own (Flutter) version of each must be **re-authored** (there is no clean copy to restore
to; recovery = rewrite for Dart/Flutter, then make config-driven per §5):

| File | What was lost |
|---|---|
| `ship.md` | Flutter merge flow, `main` base branch, `flutter-developer`/`supabase-developer` dispatch, Dart high-risk globs (`supabase/migrations`, `apps/mobile/lib/…`), `flutter test` build gate. |
| `ship-v2.md` | Same as ship; TIER globs reference Realm/billing instead of Supabase/Dart equivalents. |
| `implement.md` | `flutter-developer`/`supabase-developer` role map; Dart test commands instead of `./gradlew`. |
| `regression.md` | Entire Flutter risk taxonomy — the file is a verbatim Android copy. |
| `pr-loop.md` | `flutter test` test command + Dart review dimensions instead of Kotlin/Realm. |
| `overnight.md` | Project timezone/merge window + Dart task taxonomy (Kotlin-conversion rows are meaningless here). |

**Partial recovery (body adapted, knob leaked — only the TZ/CI knob must be fixed):**
`morning.md`, `review-all-day.md`, `deps-audit.md`, `stale-prs.md` — all still emit
`Etc/GMT-3`/UTC+3, which is a smartinventory project value, not a portable default.

### 4.2 ingreview — delete (leaked top-level mirror)

`ingreview/commands/*.md` (12) and `ingreview/agents/*.md` (2) — orphan ai-infra copies
at the wrong path. Not read by Claude Code; they only add drift surface. Remove in the
cutover (out of scope for this read-only pass).

### 4.3 ai-infra — de-contaminate (the canon is smartinventory-flavoured)

ai-infra is not project-neutral. Per-file Android/TZ/branch counts in `ai-infra/commands/`:

| File | Android | UTC+3 | `develop` |
|---|---:|---:|---:|
| `ship` | 15 | 10 | 8 |
| `regression` | 5 | 0 | 8 |
| `stale-prs` | 0 | 1 | 7 |
| `implement` | 9 | 0 | 2 |
| `deps-audit` | 9 | 2 | 0 |
| `pr-loop` | 5 | 0 | 1 |
| `ship-v2` | 4 | 1 | 1 |
| `overnight` | 3 | 4 | 1 |
| `review-cycle-to-pr` | 3 | 0 | 0 |
| `review-all-day` | 2 | 6 | 0 |
| `coverage` / `wrap` | 4 / 4 | 0 | 0 |

Every count above is a hardcoded specific that must move to config (§5.2) before the body
can be called "portable."

### 4.4 smartinventory — no loss

smartinventory is the source; its bodies are intact. (It has, in places, *drifted ahead*
of ai-infra — `ship`, `ship-v2`, `implement`, `pr-loop`, `review-cycle-to-pr`, `morning`
differ — so even the "canon" in ai-infra is a stale smartinventory snapshot.) **This drift
is ongoing:** during this audit, smartinventory **#2037** (`6da6190`) revised `ship.md`
(+39/−10) and `ship-v2.md` again for vendor+model attribution (issue #2036), without any
corresponding ai-infra/ingreview update. This is the recurring cost the thin-consumer model
eliminates — every smartinventory ship PR silently re-widens the divergence the audit measures.

---

## 5. Portability re-classification & config extraction

### 5.1 Mislabeled files

| File | Issue's tier | Reality | Recommendation |
|---|---|---|---|
| `post-issue-1-regression` | C (project-only) | Present in **ai-infra** + synced to ingreview | **Confirm Tier-C.** Remove from `ai-infra/commands/`; never sync. Each project keeps its own. |
| `pr-loop` | A (generic, as-is) | Hardcodes `./gradlew`, Realm, Kotlin, `develop` | **Reclassify A→B.** Needs `build_gate_cmd`, `tier3_globs`, `base_branch`. |
| `overnight` | A (generic, as-is) | Hardcodes UTC+3 merge window + Kotlin task rows | **Reclassify A→B.** Needs `timezone`, `merge_window`. |
| `stale-prs` | A (generic, as-is) | Hardcodes `develop` + UTC+3 date | **Reclassify A→B.** Needs `base_branch`, `timezone`. |
| `morning` | A (generic, as-is) | Hardcodes UTC+3 window + deferral path | **Reclassify A→B.** Needs `timezone`, `merge_window`. |
| `flake-audit` | A (generic, as-is) | Hardcodes CI workflow names (`Android CI`/`Web CI` → `Flutter CI`/`Supabase CI`) + base branch | **Reclassify A→B.** Needs `ci_workflows`, `base_branch`. |
| `ci-check` | A | Truly neutral (0/0/0) | **Keep Tier-A.** ✅ |
| `sync-to-ai-infra` | A | Neutral (its hardcoded `ai-infra`/`smartinventory` names are intrinsic to its job) | **Keep Tier-A** (but it is the mechanism being retired). |

> Net: the genuine "Tier-A, ship as-is" set is small — `ci-check`, `triage`,
> `review-cycle-to-pr`, `wrap` (and `sync-to-ai-infra`, soon retired). Everything else the
> issue listed as Tier-A actually carries project state and belongs in Tier-B.

### 5.2 Knobs to extract into the project-config schema

The current schema has `owner/repo/default_branch/platform/build_test/build_lint/
platform_agents/substitutions/skip_commands`. To make the Tier-B bodies neutral, add:

| New knob | Source (hardcoded today) | smartinventory value | ingreview value |
|---|---|---|---|
| `base_branch` | `develop` literals in ship/stale-prs/overnight | `develop` | `main` (already `default_branch` — wire it in) |
| `timezone` | `Etc/GMT-3` in ship/overnight/morning/review-all-day/deps-audit/stale-prs | `Etc/GMT-3` | project-appropriate (the issue treats UTC+3 in ingreview as a defect) |
| `merge_window` | `06:00–01:30` / night `01:30–06:00` in ship/overnight | `01:30–07:00` no-merge | per project |
| `implementer_agents` (role→agent map) | `android-developer`/`web-developer` switch in ship/implement | `{android: android-developer, web: web-developer}` | `{mobile: flutter-developer, backend: supabase-developer}` |
| `tier3_globs` (high-risk paths) | `android/.../object/db` (Realm), `lifecycle/`, `ListFragment.kt`, billing, `.github/workflows/` | Realm/billing/lifecycle globs | `supabase/migrations`, `apps/mobile/lib/…`, edge-fn globs |
| `docs_gate_paths` / `docs_only_allowlist` | inline in ship | SI paths | ingreview paths |
| `ci_workflows` (name→path-glob) | `Android CI`/`Web CI` in flake-audit/ship | `{Android CI, Web CI}` | `{Flutter CI, Supabase CI}` |
| `build_gate_cmd` | `./gradlew test lint …` inline | `./gradlew …` | `flutter test` / `flutter analyze` (partly in `build_test`/`build_lint`) |
| `sot_doc` | `AGENTS.md` references | `AGENTS.md` | `AGENTS.md` (same, but make it a knob) |

(`delegate`/`jury` defaults are already config-ish and can move next to these.)

---

## 6. Per-file recommended action

Legend: **keep-in-ai-infra** = body is/should be project-neutral and live canonically in
ai-infra · **extract-to-config** = keep body in ai-infra but move hardcoded specifics to
the project config · **project-only** = belongs in the project, never synced.

| File | Tier (corrected) | Recommended action |
|---|---|---|
| `ci-check` | A | **keep-in-ai-infra** (already neutral) |
| `triage` | A | **keep-in-ai-infra** |
| `review-cycle-to-pr` | A | **keep-in-ai-infra** (scrub the 3 Android refs in canon) |
| `wrap` | A | **keep-in-ai-infra** (scrub 4 Android refs in canon) |
| `sync-to-ai-infra` | A | **keep-in-ai-infra** (retire in Phase 5) |
| `ship` | B | **extract-to-config** (branch, timezone, merge_window, agents, tier3_globs, build_gate_cmd, ci_workflows) |
| `ship-v2` | B | **extract-to-config** (same knobs as ship) |
| `implement` | B | **extract-to-config** (implementer_agents, build_gate_cmd, tier3_globs) |
| `regression` | B | **extract-to-config** (tier3_globs, platform risk taxonomy via config) |
| `coverage` | B | **extract-to-config** (build/coverage cmd per platform) |
| `ui-test` | B | **extract-to-config** (UI test runner per platform) |
| `deps-audit` | B | **extract-to-config** (timezone, scope labels) |
| `review-all-day` | B | **extract-to-config** (timezone) |
| `pr-loop` | B (was A) | **extract-to-config** (build_gate_cmd, tier3_globs, base_branch) |
| `overnight` | B (was A) | **extract-to-config** (timezone, merge_window) |
| `stale-prs` | B (was A) | **extract-to-config** (base_branch, timezone) |
| `morning` | B (was A) | **extract-to-config** (timezone, merge_window) |
| `flake-audit` | B (was A) | **extract-to-config** (ci_workflows, base_branch) |
| `post-issue-1-regression` | C | **project-only** — remove from ai-infra; never sync |
| `android-build` | C | **project-only** (smartinventory) — already correct |
| `web-check` | C | **project-only** (smartinventory) — already correct |

### Structural follow-ups (Phase 0/5, not this pass)

1. **Delete the leaked top-level mirror** `ingreview/commands/` + `ingreview/agents/`
   (14 orphan files at the wrong path).
2. **Add a divergence guard** to any interim sync (3-way base/current/new compare; refuse
   to overwrite an adapted file) — Phase 0 of #2035.
3. **Wire the config in.** Today even the existing `substitutions`/`build_test` keys are
   unused by the bodies; config-driven rendering is the prerequisite for thin-consumer.

---

## 7. Method / reproducibility

- Compared `smartinventory/.claude/commands/`, `ai-infra/commands/`, and
  `ingreview/.claude/commands/` for all 21 Tier-A/B/C files via `md5sum` (pairwise
  equality) and `diff` (line-level).
- Token scans: Android (`kotlin|realm|gradle|./gradlew|paparazzi|android-developer|web-developer|.aab|espresso|firebase`),
  Flutter (`flutter|dart|.dart|supabase|pubspec|pub get|deno`), timezone (`UTC+3|Etc/GMT-3|GMT-3`),
  branch (`develop`).
- Project configs read from `ai-infra/projects/{smartinventory,ingreview}.json`.
- Snapshot refs: smartinventory `origin/develop` @ `6da6190` (post-#2037); ai-infra and
  ingreview at their audit-branch tips. `ship`/`ship-v2` checksums marked ⤴ reflect `6da6190`.
- **No writes** to any command file, no sync run, no downstream push. Audit only.
