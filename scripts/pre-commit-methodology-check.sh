#!/usr/bin/env bash
# ============================================================================
# RugMunch Methodology Leak Detection — Pre-Commit Hook
# "Code, Not Methods" — scans staged changes for proprietary method leaks
# ============================================================================
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
# Patterns that indicate proprietary method leaks
LEAK_PATTERNS=(
    # Internal hostnames
    'talos\.rugmunch\.io'
    'hydra\.rugmunch\.io'
    'cinnabox\.rugmunch\.io'
    'netcup\.'
    'contabo\.'

    # Internal IP ranges
    '100\.64\.'
    '172\.(1[6-9]|2[0-9]|3[01])\.'

    # Gopass paths (secrets manager paths)
    'rmi/[a-z]'
    'rmi/'

    # Internal filesystem paths
    '^/root/backend'
    '^/root/rmi'
    '^/srv/data'
    '^/srv/rmi'

    # Infrastructure-specific patterns
    'systemd-oomd'
    'cloudflared'
    'proprietary.*method'
    'admin\.rugmunch\.io'
)

# File patterns to scan (staged changes only)
INCLUDE_EXTENSIONS='\.(md|yml|yaml|py|ts|js|sh|json|toml|env|txt|cfg|conf)$'

# ── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ── Functions ──────────────────────────────────────────────────────────────
check_staged() {
    local found=0
    local violations=""

    # Get staged files
    local staged_files
    staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)

    if [ -z "$staged_files" ]; then
        echo -e "${GREEN}✓ No staged files to check${NC}"
        return 0
    fi

    echo "🔍 Scanning $(echo "$staged_files" | wc -l) staged files for methodology leaks..."

    while IFS= read -r file; do
        # Skip binary files and files outside our extension range
        if ! echo "$file" | grep -qE "$INCLUDE_EXTENSIONS"; then
            continue
        fi

        # Skip files in safe paths
        if echo "$file" | grep -qE '\.git/|node_modules/|venv/|\.venv/|__pycache__/'; then
            continue
        fi

        # Read staged content
        local content
        content=$(git diff --cached "$file" 2>/dev/null || true)

        # Check each leak pattern
        for pattern in "${LEAK_PATTERNS[@]}"; do
            if echo "$content" | grep -qE "$pattern"; then
                # Extract the actual matching lines (added lines in diff)
                local matches
                matches=$(echo "$content" | grep -nE "^\+.*$pattern" | sed 's/^+//' || true)
                if [ -n "$matches" ]; then
                    echo -e "${RED}⚠  METHODOLOGY LEAK in $file:${NC}"
                    echo "$matches" | while IFS= read -r line; do
                        echo -e "  ${RED}→ $line${NC}"
                    done
                    found=$((found + 1))
                    violations="$violations $file"
                fi
            fi
        done
    done <<<"$staged_files"

    if [ "$found" -gt 0 ]; then
        echo ""
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}❌ METHODOLOGY LEAK DETECTED: $found violation(s)${NC}"
        echo -e "${RED}   Files: $violations${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "Policy: METHODOLOGY-PROTECTION.md — 'Code, Not Methods'"
        echo "These patterns reveal proprietary operations. Fix before commit."
        echo "  - Remove internal hostnames/IPs"
        echo "  - Replace gopass paths with env var references"
        echo "  - Replace internal paths with generic /path/to/ placeholders"
        echo ""
        return 1
    fi

    echo -e "${GREEN}✓ Methodology check passed — no leaks detected${NC}"
    return 0
}

# ── Main ────────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " RugMunch Methodology Leak Detector"
echo " 'Code, Not Methods'"
echo "========================================"

check_staged
exit_code=$?

if [ $exit_code -ne 0 ]; then
    exit 1
fi

exit 0
