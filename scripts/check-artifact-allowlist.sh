#!/bin/sh
# Pre-publish check: every git-tracked file must be inside the shipping
# allowlist.  Run locally before tagging, and in CI on every PR:
#   bash scripts/check-artifact-allowlist.sh
#
# This matters more here than upstream: reference/hubspot-claude/ is a full
# clone of the source plugin sitting in the working tree (gitignored). Tracking
# it by accident would ship someone else's repo inside this plugin's artifact,
# so an unrecognised tracked file is a hard CI failure rather than a warning.
#
# Allowlist: src/ tests/ bin/ hooks/ skills/ .claude-plugin/ .github/
# scripts/check-artifact-allowlist.sh, the tracked files under docs/,
# README.md, CHANGELOG.md, LICENSE, pyproject.toml, .gitignore
set -u

allow_regex='^(src/|tests/|bin/|hooks/|skills/|\.claude-plugin/|\.github/|scripts/check-artifact-allowlist\.sh$|docs/architecture\.md$|docs/phase-1-build-plan\.md$|docs/phase-2-build-plan\.md$|docs/hosted-setup\.md$|README\.md$|app\.py$|vercel\.json$|requirements\.txt$|CHANGELOG\.md$|LICENSE$|pyproject\.toml$|\.gitignore$)'

bad=$(git ls-files | grep -Ev "$allow_regex")
if [ -n "$bad" ]; then
  echo "ERROR: tracked files outside the shipping allowlist:" >&2
  printf '%s\n' "$bad" | sed 's/^/  /' >&2
  echo "  Fix: git rm --cached <path> && add it to .gitignore," >&2
  echo "  or add it to allow_regex above if it genuinely ships." >&2
  exit 1
fi

echo "OK: all $(git ls-files | wc -l | tr -d ' ') tracked files are within the shipping allowlist."
