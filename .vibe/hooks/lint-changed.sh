#!/usr/bin/env bash
# post_agent hook "lint-changed": lint and format-check the Python files this
# turn changed with the project's linter, ruff.
#
# Runner selection keeps CI parity while favouring fast feedback:
#   1. host ruff (uv tool install ruff==<pin>) when its version matches the
#      pin in pyproject.toml -- byte-identical results, milliseconds;
#   2. otherwise ruff inside the Linux development container
#      (docs/development.md), which is what CI runs;
#   3. neither available -> skip silently.
#
# Contract with Vibe:
#   - no changed Python files, or no runner available -> exit 0, silent (skip);
#   - ruff exit code 1 -> findings: print {"decision":"deny","reason":...} on
#     stdout (the agent fixes them before finishing) and exit 0;
#   - any other non-zero exit -> infrastructure failure: exit 0, silent (fail
#     open - never block a turn on a tooling hiccup).

set -u

# Run from the repository root, regardless of the session's subfolder.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root" || exit 0

# ---- 1. Collect changed Python source files (tracked-modified + untracked). ----
mapfile -t candidates < <(
    {
        git diff --name-only HEAD --
        git ls-files --others --exclude-standard
    } 2>/dev/null | sort -u | grep -E '\.py$' || true
)
[ "${#candidates[@]}" -eq 0 ] && exit 0

files=()
for f in "${candidates[@]}"; do
    # Auto-generated migrations are excluded from ruff (pyproject.toml); skip
    # them explicitly, since per-file invocation would otherwise lint them.
    case "$f" in
        */migrations/*|migrations/*) continue ;;
    esac
    [ -f "$f" ] || continue # ignore deleted/renamed-away files
    files+=("$f")
done
[ "${#files[@]}" -eq 0 ] && exit 0

# ---- 2. Pick the runner: pinned host ruff, else the container. ----
pinned="$(sed -n 's/.*ruff==\([0-9.][0-9.]*\).*/\1/p' pyproject.toml | head -n 1)"
ruff_cmd=()
if [ -n "$pinned" ] && command -v ruff >/dev/null 2>&1; then
    host_version="$(ruff --version 2>/dev/null | awk '{print $2}')"
    if [ "$host_version" = "$pinned" ]; then
        ruff_cmd=(ruff)
    fi
fi
if [ "${#ruff_cmd[@]}" -eq 0 ]; then
    command -v docker >/dev/null 2>&1 || exit 0
    [ -f compose.development.yaml ] || exit 0
    docker info >/dev/null 2>&1 || exit 0
    docker image inspect growbook:development >/dev/null 2>&1 || exit 0
    ruff_cmd=(docker compose -f compose.development.yaml run --rm -T app ruff)
fi

# ---- 3. Run the linter and format check on just those files. ----
out="$(mktemp)"
trap 'rm -f "$out"' EXIT

"${ruff_cmd[@]}" check --force-exclude -- "${files[@]}" </dev/null >"$out" 2>&1
rc_check=$?
"${ruff_cmd[@]}" format --check --force-exclude -- "${files[@]}" </dev/null >>"$out" 2>&1
rc_format=$?

if [ "$rc_check" -ne 1 ] && [ "$rc_format" -ne 1 ]; then
    # Clean (0) or infrastructure failure (>= 2): pass through silently.
    exit 0
fi

# ---- 4. Findings: deny with the (capped, JSON-escaped) lint output. ----
reason="$(cat "$out")"
cap=6000
[ "${#reason}" -gt "$cap" ] && reason="${reason:0:$cap}
... (lint output truncated)"

# Escape for embedding in a JSON string: backslash, double quote, newlines.
escaped="$(printf '%s' "$reason" \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
    | awk 'NR > 1 { printf "\\n" } { printf "%s", $0 }')"

printf '{"decision":"deny","reason":"%s"}\n' "$escaped"
exit 0
