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

## The merge path: GraphQL, or REST when the endpoint is blocked

`keel merge` reads the pull request and merges it through `gh`, and its read-only drift check,
`keel verify-merge`, reads the pull request the same way. `gh pr view --json` and `gh pr merge`
go over GitHub's **GraphQL** endpoint. On a host whose egress proxy serves the REST API and
blocks GraphQL, both commands take `--transport auto|graphql|rest`:

| `--transport` | behaviour |
| --- | --- |
| `auto` (default) | GraphQL first; if a read fails, a probe asks whether the endpoint is reachable at all, and only a blocked endpoint switches the run to REST |
| `graphql` | GraphQL only |
| `rest` | REST only; the probe never runs |

This is a choice of **wire** inside the `gh` transport above, not a third transport. For
`keel merge`, the merge claim, the window re-check, the rollup semantics, the evidence gate and
the SHA-pinned gates-pass are the same objects on either wire, and the merge payload records
which one answered (`transport: gh-graphql` or `gh-rest`); the transport is settled by the
reads, before anything is written, so a merge is never retried over a second wire.
`keel verify-merge` writes nothing, and reports the wire it used as `transport` in its drift
report. See [`keel merge`](cli.md#transport-graphql-or-rest-when-the-endpoint-is-blocked) for the
details.

## Boundary

Transport selection is runtime-owned, not project-owned. Projects may require GitHub side
effects through policy, but they should not duplicate `gh` vs MCP mapping tables in command
text or extensions.
