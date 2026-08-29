# Agent Safety Policy — AI-assisted GitHub Actions (OpenCode Zen)

This policy governs the four agentic workflows under `.github/workflows/`
(`ai-pr-review`, `ai-ci-diagnose`, `ai-fix-pr`, `ai-issue-to-draft-pr`). It is the
authoritative safety contract; workflow files implement it.

## 1. Threat model

Prompt injection can arrive from:

- **Pull request content**: title, body, review comments, diff, committed files.
- **Issue content**: title, body, comments, linked artifacts.
- **Source code & docs**: any file in the repo may contain "ignore previous
  instructions" style injections.
- **CI logs**: failed-step output may contain injected directives or leaked secrets.
- **Dependencies / generated files**: supply-chain and model-generated output.

All of the above are treated as **untrusted data**, never as instructions. The
agent is explicitly instructed (in every prompt) to ignore in-content instructions
and to never disclose secrets.

## 2. Least privilege & token scoping

- `OPENCODE_API_KEY` is the only LLM secret; injected from a GitHub Actions secret,
  never printed, logged, or committed.
- Each workflow sets the **minimum** `permissions:` (read-only unless write is
  required and gated).
- Write-capable workflows (`ai-fix-pr`, `ai-issue-to-draft-pr`) require:
  - manual `workflow_dispatch` (no automatic triggers for writes);
  - an authorized actor (collaborator with write/admin, or org member);
  - `AI_AGENT_WRITE_ENABLED=true` for any actual push/commit.

## 3. Fork PR isolation

- `ai-pr-review` runs only for **same-repository** PRs. For fork PRs the LLM job is
  skipped, so `OPENCODE_API_KEY` is never exposed to untrusted fork code.
- `ai-fix-pr` rejects fork/externally-owned head branches entirely (cannot push to
  a branch we do not control).

## 4. Why write workflows are manual-only

Automatic triggers for write behavior would let any PR/issue author cause repo
mutations. Manual dispatch by an authorized maintainer is the intentional gate.
Read-only workflows (review, diagnose) may be automatic because they cannot mutate
state.

## 5. Protected paths & sensitive changes

The agent must NOT modify, unless explicitly named by the maintainer AND approved:

- `.github/workflows/**`, `.github/actions/**`, `.github/ai/**`
- secret/configuration management, IAM / access-control / identity / permission config
- production deployment configuration, release/publishing configuration
- Terraform/OpenTofu root modules, state/backend configuration

Sensitive issue work additionally requires the `agent:approved-sensitive` label.

## 6. Default dry-run

- `ai-fix-pr` defaults to `dry_run: true`. Writes happen only when
  `AI_AGENT_WRITE_ENABLED=true` AND the authorized actor explicitly requests write.
- In dry-run the agent produces a patch artifact + comment; nothing is pushed.

## 7. Artifact / log retention & redaction

- Log excerpts and diffs are truncated (default 60000 chars for comments).
- Secrets/tokens are redacted before being quoted in any output.
- Artifacts (patches) may contain source diffs; keep default retention short
  (GitHub default) and avoid attaching env dumps.

## 8. Cost, timeout, iteration, concurrency limits

- Every workflow job sets `timeout-minutes` (default 20; write jobs 30).
- `ai-pr-review` uses `concurrency` to cancel obsolete runs for the same PR.
- The agent is bounded by `--max-turns` (default 20) via the wrapper.
- `ai-pr-review` runs on every `synchronize`; keep it fast and read-only.

## 9. Human review required

- AI output is advisory. No workflow merges, approves, dismisses reviews, alters
  branch protection, or changes labels. Draft PRs require human review before merge.

## 10. Emergency disable

1. Disable the workflow in **Settings → Actions → Disabled workflows** (or delete
   the file).
2. Set repository variable `AI_AGENT_WRITE_ENABLED=false` (or delete it).
3. If `OPENCODE_API_KEY` may have been exposed (e.g., a compromised runner), rotate
   it immediately in **Settings → Secrets**.
