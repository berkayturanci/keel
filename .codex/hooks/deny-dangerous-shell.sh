#!/usr/bin/env bash
set -euo pipefail

payload="$(cat || true)"

command_text="$(
  PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys

payload = os.environ.get("PAYLOAD", "")
if not payload.strip():
    sys.exit(0)

try:
    data = json.loads(payload)
except json.JSONDecodeError:
    print(payload)
    sys.exit(0)

keys = (
    ("tool_input", "command"),
    ("tool_input", "cmd"),
    ("input", "command"),
    ("input", "cmd"),
    ("arguments", "command"),
    ("arguments", "cmd"),
    ("params", "command"),
    ("params", "cmd"),
)

for path in keys:
    value = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            break
        value = value[key]
    else:
        if isinstance(value, str) and value.strip():
            print(value)
            sys.exit(0)

for key in ("command", "cmd"):
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, str) and value.strip():
        print(value)
        sys.exit(0)
PY
)"

if [ -z "${command_text// }" ]; then
  exit 0
fi

normalized_text="$(printf '%s' "$command_text" | tr -s '[:space:]' ' ' | sed 's/ *| */|/g')"

patterns=(
  'git push --force'
  'git push -f'
  'git push --force-with-lease'
  'git push origin :'
  'git push origin --delete'
  'git push --delete'
  'git reset --hard'
  'git clean -f'
  'git filter-branch'
  'git update-ref -d'
  'git branch -D develop'
  'git branch -D main'
  'rm -rf /'
  'rm -rf ~'
  'rm -rf $HOME'
  'rm -rf --no-preserve-root'
  'sudo rm'
  'sudo '
  'gh repo delete'
  'gh release delete'
  'gh secret delete'
  'gh secret set'
  'gh auth logout'
  'npm publish'
  'npm unpublish'
  'eval '
  'dd if='
  'mkfs'
  'shred'
  'chmod -R 777 /'
  'chown -R '
)

for pattern in "${patterns[@]}"; do
  case "$command_text" in
    *"$pattern"*)
      echo "BLOCKED by .codex/hooks/deny-dangerous-shell.sh: command matched '$pattern'" >&2
      echo "Command: $command_text" >&2
      exit 2
      ;;
  esac
done

case "$command_text" in
  *'gh api '*'-X DELETE'*|*'gh api '*'--method DELETE'*|*'curl '*' -X DELETE'*|*'curl '*'--request DELETE'*|*'curl '*'| sh'*|*'curl '*'| bash'*|*'wget '*'| sh'*|*'wget '*'| bash'*|*'curl '*'|sh'*|*'curl '*'|bash'*|*'wget '*'|sh'*|*'wget '*'|bash'*)
    echo "BLOCKED by .codex/hooks/deny-dangerous-shell.sh: command matched a dangerous compound shell pattern" >&2
    echo "Command: $command_text" >&2
    exit 2
    ;;
esac

case "$normalized_text" in
  *'curl '*'|sh'*|*'curl '*'|bash'*|*'wget '*'|sh'*|*'wget '*'|bash'*)
    echo "BLOCKED by .codex/hooks/deny-dangerous-shell.sh: command matched a dangerous compound shell pattern" >&2
    echo "Command: $command_text" >&2
    exit 2
    ;;
esac

case "$command_text" in
  *'git push '*'--force'*|*'git push '*' -f'*|*'git push '*'--force-with-lease'*)
    echo "BLOCKED by .codex/hooks/deny-dangerous-shell.sh: command matched a dangerous git force-push pattern" >&2
    echo "Command: $command_text" >&2
    exit 2
    ;;
esac

exit 0
