# GitHub transport

Keel resolves GitHub access once from the runtime capability report and exposes a normalized
transport contract to commands and adapters.

## Selection order

1. `gh` — selected when both `gh` and `gh-auth` are available.
2. `mcp` — selected when `github-mcp` is available and authenticated `gh` is not.
3. `none` — selected when no GitHub transport is available.

The selected transport is reported in `keel capabilities`, `keel plan --json`, and
`keel ship` output.

## Normalized operation capabilities

The contract reports these operation names:

| operation | meaning |
|---|---|
| `issue_read` | Read issue metadata and bodies. |
| `issue_write` | Create, edit, label, comment on, or close issues. |
| `pr_read` | Read PR metadata, draft state, mergeability, and branch data. |
| `pr_write` | Create, edit, label, comment on, or update PRs. |
| `pr_merge` | Merge a PR. |
| `check_runs` | Read normalized check run or status rollup data. |
| `raw_actions_logs` | Read raw workflow/job logs. |
| `labels` | Read and write labels where write access is available. |
| `comments` | Read and write issue/PR comments where write access is available. |
| `reviews` | Read and write PR reviews where write access is available. |
| `files` | Read PR file lists and changed file metadata. |

`gh` currently reports full support. The MCP fallback reports the shared read/comment/list
surface but marks `pr_merge`, `check_runs`, and `raw_actions_logs` as degraded until
concrete host MCP operations prove those actions are available. Commands must treat
unsupported operations as explicit degradation or failure, not as hidden best-effort
behavior. For CI handling this means a command can still surface the check name or details
URL when the host provides it, but it must not claim a normalized rollup or raw log stream
unless the selected transport reports that operation.

## Normalized fields

The contract also exposes `normalized_fields` so command adapters share one vocabulary for
cross-transport data: `issue_labels`, `pr_state`, `draft_state`, `mergeable_state`,
`check_runs`, `comments`, `reviews`, `files`, and `merge_operations`. A transport may still
mark an operation degraded when the runtime cannot provide the data or side effect behind
that normalized field.

## JSON shape

```json
{
  "transport": "gh",
  "available": true,
  "normalized_fields": [
    "issue_labels",
    "pr_state",
    "draft_state",
    "mergeable_state",
    "check_runs",
    "comments",
    "reviews",
    "files",
    "merge_operations"
  ],
  "capabilities": {
    "issue_read": true,
    "pr_merge": true,
    "check_runs": true,
    "raw_actions_logs": true
  },
  "degraded": [],
  "reason": "authenticated GitHub CLI"
}
```

The full `capabilities` object always contains every operation in the table above.

## Boundary

Transport selection is runtime-owned, not project-owned. Projects may require GitHub side
effects through policy, but they should not duplicate `gh` vs MCP mapping tables in command
text or extensions.
