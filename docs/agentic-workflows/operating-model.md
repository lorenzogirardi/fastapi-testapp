# Operating Model

How the agentic workflows are triggered, what the agent does, what humans must do,
and how to operate the system safely. Target readers: technical leads and
product/delivery stakeholders.

**Confidence legend:** *(Verified)* observed in workflow files / run metadata;
*(Inferred)* deduced; *(Recommended)* suggested; *(Unknown)* not available.

## Triggering conditions

| Workflow | Trigger | How invoked | Evidence |
|---|---|---|---|
| `ai-pr-review` | `pull_request` opened/reopened/ready_for_review/synchronize; also manual | Automatic on PR; manual via `workflow_dispatch` with `pr_number` | `ai-pr-review.md:3-10` |
| `ai-ci-diagnose` | manual only | `workflow_dispatch` with `pr_number`, `run_id` | `ai-ci-diagnose.md:4-11` |
| `ai-fix-pr` | manual only | `workflow_dispatch` with `pr_number`, `instruction`, `dry_run` (default true) | `ai-fix-pr.md:4-15` |
| `ai-issue-to-draft-pr` | manual only | `workflow_dispatch` with `issue_number` | `ai-issue-to-draft-pr.md:4-8` |

Notes:
- `ai-pr-review` is the only **automatically** triggered workflow. The other three
  are opt-in manual dispatches, which limits blast radius and token spend.
  *(Verified from `on:` blocks.)*
- Each workflow uses `concurrency` with `cancel-in-progress: true` keyed by the
  target PR/issue, so re-triggers cancel stale runs. *(Verified.)*

## Expected agent actions and output

- **Review:** post a Markdown review comment with severity-ranked, file:line
  findings and a validation command. *(Verified prompt + PR #8 output.)*
- **Diagnose:** post a comment explaining the failing check, root cause, and a fix.
- **Fix PR:** in `dry_run` (default) post a proposal; if `dry_run=false`, commit
  and push to the PR branch (gated by `update-pull-request` safe-output).
- **Issue→draft PR:** create branch `ai/issue-<n>`, implement, open a **draft** PR
  (gated by `create-pull-request` safe-output).

## What happens on problems

| Condition | Behavior | Evidence / note |
|---|---|---|
| Uncertain / insufficient info | Agent posts findings only; does not invent changes | prompt: "Require evidence"; "If not, post a comment … and stop" |
| Cannot reproduce failure | Reports analysis; suggests commands | prompt rule |
| Sensitive area touched (write workflows) | Protected paths excluded; never force-push/rewrite history | prompt: "never touch protected paths" |
| Budget / timeout | `timeout-minutes` per workflow (20/20/30/40) cancels the run | frontmatter `timeout-minutes` |
| Validation failure | Agent reports; human decides | no auto-apply enforced |

## Human responsibilities

- **Before merge:** read the agent comment, verify findings against the diff, run
  the repo's own tests/lint, and confirm no secret leakage.
- **Approvals:** merge/approval remains fully human. The workflows never grant
  write scopes broadly; where writes occur they are mediated by safe-outputs.
- **Write workflows (`ai-fix-pr`, `ai-issue-to-draft-pr`):** a human should review
  the generated diff before completing/merging the resulting PR.

## Lifecycle table

| Phase | Trigger / input | Agent responsibility | Deterministic checks | Human decision | Output / evidence |
|---|---|---|---|---|---|
| Trigger | PR opened / manual dispatch | — | none | none | GitHub Actions run created |
| pre_activation | run start | — | auth, guardrails, budget | — | job `pre_activation` success |
| activation | PR/issue context | build prompt + context | — | — | job `activation` success |
| agent | prompt + repo | call LLM, analyze, draft output | suggested `pytest`/`flake8` | — | job `agent` success |
| safe_outputs | agent output | request safe output | gating by gh-aw | — | comment / PR posted |
| review | posted comment | — | — | review & merge | PR timeline |

## RACI (repository-generic roles)

| Activity | Author | Agent (LLM) | Reviewer/Maintainer | Platform/Security |
|---|---|---|---|---|
| Open PR / issue | R | — | — | — |
| Run agent review | — | R/A | — | A (config) |
| Post comment | — | R (via safe-output) | — | A (gate) |
| Merge / release | — | — | R/A | C |
| Secret & provider config | — | — | C | R/A |
| Audit & incident response | — | — | C | R |

R=Responsible, A=Accountable, C=Consulted.

## Definition of Done for an agent-assisted change

- [ ] Agent completed its action (review posted / fix pushed / draft PR opened).
- [ ] Repository CI/quality gates pass (`pytest`, `flake8`, any branch checks).
- [ ] Human reviewer approved the diff and the agent output.
- [ ] Product acceptance confirmed where required.
- [ ] Change merged through normal processes (no bypass of branch protection).
- [ ] No secret or credential leaked in comments, logs, or artifacts.

## Improving outcomes (what to put in PRs / issues)

- Clear title and description; link the related issue.
- Acceptance criteria / expected behavior stated explicitly (helps `ai-fix-pr`
  and `ai-issue-to-draft-pr`).
- For `ai-ci-diagnose`, provide the failing `run_id` and `pr_number`.

## Escalation model

| Situation | Action |
|---|---|
| Suspicious agent output / possible prompt injection | Do not act on it; report in PR; consider disabling the workflow dispatch |
| False positives / low-value review | Tune the prompt in the `.md` source; recompile |
| Unsafe proposed change | Reject; rely on read-only default for review |
| Stuck / failing run | Re-run; inspect `agent` artifact logs (`agent-stdio.log`, `awf-config.json`) |
| Secret exposure concern | Rotate `OPENCODE_API_KEY`; review run artifacts for leakage |
| Model/provider outage | Workflow fails safely (no writes); fall back to manual review |

## Repeat runs / idempotency

- `concurrency` + `cancel-in-progress` prevents duplicate concurrent runs on the
  same PR/issue. *(Verified.)*
- `ai-pr-review` runs again on `synchronize`, so each push to a PR re-reviews.
- Comments are not auto-deduplicated by the workflow itself; repeated runs may
  post multiple comments. *(Inferred; mitigation: safe-output marker not yet
  implemented in these workflows — see improvements.)*
