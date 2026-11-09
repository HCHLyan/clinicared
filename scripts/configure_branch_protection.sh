#!/usr/bin/env bash
# Configure branch protection for main and develop (US 05 T1).
# Requires: gh CLI authenticated as a repo admin. Usage: ./scripts/configure_branch_protection.sh owner/repo
set -euo pipefail
REPO="${1:?usage: $0 owner/repo}"

protect() {
  local branch="$1" approvals="$2"
  gh api -X PUT "repos/${REPO}/branches/${branch}/protection" \
    -H "Accept: application/vnd.github+json" \
    --input - <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Branch naming & flow", "lint", "secrets-scan", "unit-tests", "integration-tests", "dag-validation", "docker-build"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": ${approvals},
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
  echo "protected ${branch} (${approvals} approvals)"
}

protect main 2
protect develop 1
