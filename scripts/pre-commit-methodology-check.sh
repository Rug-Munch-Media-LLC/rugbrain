#!/bin/bash
# RugMunch Methodology Leak Detection — Pre-Commit Hook (SANITIZED for OSS)
# Canonical source: standards/scripts/pre-commit-methodology-check.sh
# 
# This is a sanitized version for public/open-source repos.
# It checks for COMMON patterns that should never be committed.
# The full version with infrastructure-specific patterns lives in
# the internal standards repo.

set -euo pipefail

# Generic forbidden patterns safe for public repos
# These detect accidental leaks without revealing our infrastructure
FORBIDDEN_PATTERNS=(
  # Generic API keys and secrets (any service)
  'api[_-]?key\s*=\s*['"'"'"]\w{20,}'
  'sk-[a-zA-Z0-9]{20,}'     # OpenAI-style keys
  'AKIA[0-9A-Z]{16}'         # AWS access keys
  'xox[baprs]-[0-9a-zA-Z]{10,}'  # Slack tokens
  'ghp_[a-zA-Z0-9]{36}'      # GitHub PATs
  'gho_[a-zA-Z0-9]{36}'      # GitHub OAuth tokens
  
  # Generic internal hostname patterns (not our specific ones)
  '\.internal\.'
  '\.int\.'
  '\.corp\.'
  
  # Generic filesystem paths that shouldn't be hardcoded
  '/home/[^/]+/\..*/(secrets|keys|certs|credentials)'
  
  # Comments and documentation markers
  'FIXME'
  'HACK'
  'XXX'
  '@nocommit'
)

FOUND=0
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)

if [ -z "$STAGED_FILES" ]; then
  echo "✅ Methodology check: clean (no staged files)"
  exit 0
fi

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  matches=$(echo "$STAGED_FILES" | xargs grep -ln "$pattern" 2>/dev/null || true)
  if [ -n "$matches" ]; then
    echo "⚠️  POTENTIAL ISSUE: $pattern"
    while IFS= read -r f; do
      echo "     $f"
    done <<< "$matches"
    FOUND=1
  fi
done

if [ "$FOUND" -eq 1 ]; then
  echo ""
  echo "⚠️  Review flagged patterns before committing."
  echo "   If these are false positives, use: git commit --no-verify"
  exit 1
fi

echo "✅ Methodology check: clean"
exit 0
