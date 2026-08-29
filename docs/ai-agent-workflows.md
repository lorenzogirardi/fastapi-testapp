# AI Agent Workflows (OpenCode Zen)

Automated, agentic GitHub Actions for this repository, powered by the OpenCode CLI
with OpenCode Zen credentials. Four workflows live under `.github/workflows/`.

## 1. Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │            GitHub Actions (this repo)         │
                         │                                               │
  PR / issue / dispatch  │   .github/workflows/                         │
  ───────────────────────▶  ├─ ai-pr-review.yml        (read-only)      │
  (manual: maintainer)   │  ├─ ai-ci-diagnose.yml      (read-only)      │
                         │  ├─ ai-fix-pr.yml           (write, gated)    │
                         │  └─ ai-issue-to-draft-pr.yml (write, gated)   │
                         │            │                                  │
                         │            ▼                                  │
                         │   .github/scripts/                           │
                         │   ├─ run-opencode-agent.sh  (HTTP client to   │
                         │   │                            Zen endpoint)   │
                         │   └─ upsert-marker-comment.sh                │
                         │            │                                  │
                         │            ▼                                  │
                         │   run-opencode-agent.sh                       │
                         │   (curl) ──▶ OpenCode Zen ENDPOINT (remote)    │
                         │   (OPENCODE_API_KEY secret, model+endpoint     │
                         │    from repo VARIABLES)                        │
                         └─────────────────────────────────────────────┘
                                  │ posts/updates
                                  ▼
                         PR / issue comments  (marker: <!-- ai-agent:* -->)
```

## 2. Workflows

| Workflow | Trigger | Can | Cannot |
|----------|---------|-----|--------|
| `ai-pr-review` | PR opened/reopened/ready_for_review/synchronize + dispatch | Post/update one review comment (read-only) | Push, branch, edit files, merge, change labels |
| `ai-ci-diagnose` | `workflow_dispatch` (pr_number, run_id), authorized actor | Diagnose failed run, post/update comment | Rerun, deploy, modify state |
| `ai-fix-pr` | `workflow_dispatch` (pr_number, instruction, dry_run) | In dry-run: propose patch + artifact + comment. In write (gated): commit & push to PR branch | Touch default branch, force-push, modify protected paths, merge/approve |
| `ai-issue-to-draft-pr` | `workflow_dispatch` (issue_number) | Branch, implement, open **draft** PR, comment | Merge/approve, deploy, publish, change secrets/perms, modify protected paths |

Manual triggers are intentionally required for any write behavior. Read-only
workflows may run automatically (review) but never mutate state.

## 3. Required GitHub repository configuration (you must do this)

1. **Secret** `OPENCODE_API_KEY` — your OpenCode Zen API key.
   Settings → Secrets and variables → Actions → New repository secret.
2. **Variable** `OPENCODE_ZEN_ENDPOINT` — full URL of the OpenCode Zen agent
   endpoint (e.g. `https://zen.opencode.ai/v1/chat/completions`). Repo variable,
   not a secret. The runner POSTs prompts here; no OpenCode binary is installed
   locally.
3. **Variable** `OPENCODE_MODEL` — the Zen model id to use (e.g. `opus-...`).
   Repo variable, not a secret. Change it without editing tracked files.
4. **Variable** `AI_AGENT_WRITE_ENABLED` = `false` (initially). Settings →
   Secrets and variables → Actions → Variables. Set to `true` only when you want
   `ai-fix-pr` to actually push (still requires an authorized actor + manual run).
3. **Branch protection** on the default branch: require a passing review / status
   before merge. The bot never bypasses protection and never merges.
4. **Labels** (optional but referenced):
   - `agent:ready` — reserved for a future safe label trigger (NOT enabled yet).
   - `agent:approved-sensitive` — allows the issue workflow to touch sensitive
     areas when explicitly set by a maintainer.

No slash-command trigger is implemented (no safe existing convention); all runs are
`workflow_dispatch` from the Actions tab.

## 4. How to invoke manually

- **PR review**: `Actions → AI PR Review → Run workflow` (optional `pr_number`).
  Or just open/update a same-repo PR.
- **CI diagnose**: `Actions → AI CI Diagnose → Run workflow`, fill `pr_number` and
  `run_id` (from the failed run URL `.../actions/runs/<run_id>`).
- **Fix PR**: `Actions → AI Fix PR → Run workflow`, fill `pr_number`, `instruction`,
  `dry_run` (default `true`).
- **Issue → draft PR**: `Actions → AI Issue to Draft PR → Run workflow`, fill
  `issue_number`.

## 5. Enabling / disabling write mode

- **Enable**: set variable `AI_AGENT_WRITE_ENABLED=true`, then run `ai-fix-pr` with
  `dry_run=false` as an authorized collaborator. The push also requires the PR head
  to belong to this repo (forks are rejected).
- **Disable immediately**: set `AI_AGENT_WRITE_ENABLED=false` (or delete it). Also
  disable the workflow in Settings → Actions, and rotate `OPENCODE_API_KEY` if
  exposure is suspected. See `.github/ai/policies/agent-safety-policy.md`.

## 6. Security model & limitations

- `OPENCODE_API_KEY` is the only LLM secret; never printed/logged/committed.
- All PR/issue/log/diff content is treated as untrusted data (prompt-injection
  resistant by instruction). Secrets in logs are redacted before quoting.
- Fork PRs never receive the LLM key in review. Write workflows reject fork heads.
- Protected paths (`.github/workflows`, `.github/actions`, `.github/ai`, IAM/deploy/
  release/Terraform-root) are blocked from being pushed by a guard step.
- The agent CANNOT merge, approve, dismiss reviews, alter protection, or change
  labels. Human review is mandatory.
- **Known limitation**: the exact OpenCode Zen request schema/response field
  (see `run-opencode-agent.sh` `TODO(CONFIRM)`) is a placeholder. Confirm the path,
  auth header, request body, and the response field that holds the agent text before
  relying on write mode. OpenCode Zen is used as a remote endpoint — no OpenCode
  binary runs on the runner, so the agent cannot directly mutate repo files; in
  write mode the response is captured as an artifact + comment (auto-applying a patch
  requires parsing the diff from the response, currently TODO).

## 7. Cost-control recommendations

- **Path filtering**: add `paths:` / `paths-ignore:` to `ai-pr-review` if you only
  want reviews on `app/**` or `tests/**`.
- **Concurrency**: `ai-pr-review` cancels obsolete runs per PR
  (`concurrency.cancel-in-progress: true`).
- **Truncation**: PR comments limited to 60000 chars; CI logs to 200000 chars.
- **Timeout**: every job sets `timeout-minutes` (20 read-only, 30 write).
- **Agent turns**: `--max-turns 20` default in the wrapper.
- **Model routing**: if OpenCode Zen supports per-task models, set them in the
  wrapper or via a repo variable; start all workflows in read-only/manual mode and
  measure false-positive rate before enabling writes.

## 8. Troubleshooting

- **Missing OpenCode binary**: no longer used — OpenCode Zen is called as a remote
  endpoint. If you still see an install step, you are on an old version; the workflow
  only needs `curl` (present on GitHub runners).
- **Endpoint unreachable / 404**: check `OPENCODE_ZEN_ENDPOINT` (repo variable) is
  the correct full URL. The request is a POST with `Authorization: Bearer
  $OPENCODE_API_KEY` and a JSON body; confirm the path with OpenCode Zen docs.
- **Wrong model**: check `OPENCODE_MODEL` (repo variable). It is sent in the request
  `model` field; change it without editing tracked files.
- **Invalid Zen credential**: agent step fails with auth error. Re-check the
  `OPENCODE_API_KEY` secret; confirm the env var name in `run-opencode-agent.sh`.
- **Token permissions**: "Resource not accessible" on comment/push → ensure the
  workflow `permissions:` block matches the action (pull-requests: write to comment;
  contents: write to push) and the actor has repo write.
- **Unavailable PR head branch**: `ai-fix-pr` checks out `refs/pull/<n>/head`; if the
  branch was force-pushed/deleted, re-run after the PR is updated.
- **Failed test commands**: the agent stops after a small fixed number of attempts
  and reports failure; no infinite loop. Check `/tmp/tests.txt` in the run log.
