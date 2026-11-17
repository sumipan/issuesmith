#!/usr/bin/env bash
# develop/review — Issue #${issue_number}
set -euo pipefail
exec python3 -m issuesmith.lines.develop.review dispatch \
  "issue_number=${issue_number}" \
  "base_branch=${base_branch}" \
  "branch=${branch}" \
  "allow_paths=${allow_paths}" \
  "worktree_path=${worktree_path}" \
  "target_repo=${target_repo}" \
  "issue_repo=${issue_repo}" \
  "workflow_name=develop" \
  "handler_name=develop"