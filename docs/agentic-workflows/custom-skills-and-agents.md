# Adapting custom skills and agents to a gh-aw workflow

How to plug **skill playbooks** and **custom / sub-agents** into a GitHub
Agentic Workflow (`github/gh-aw`), using the `security-review` workflow in this
repo as the worked example.

**Confidence legend:** *(Verified)* observed in this repo's workflow files / run
metadata; *(Docs)* from `github.github.io/gh-aw`; *(Recommended)* guidance.

### Worked example — `security-review` as tuned *(Verified, run 33766554943)*

| Setting | Value | Why |
|---|---|---|
| Skill source | `mukul975/Anthropic-Cybersecurity-Skills` cloned in a pre-step, pinned `--branch v1.3.0` | reproducible; new skills only on a tag bump |
| `max_skills` | `12` (input default) | ~28 min run with DeepSeek V4 Flash + 120k source |
| Coverage mix | ≥ ⌈0.7·N⌉ application-layer, ≤ ⌊0.3·N⌋ infra (prompt-enforced) | keep it a code review, not an infra scan (§2d) |
| `timeout-minutes` | `55` | a mid-run provider stall of ~14 min was observed; 40 min was not enough (§7) |
| Source budget | ~120k chars in the prompt | shorter per-skill calls |
| Report delivery | `post-steps:` cats `security-review.md` into `$GITHUB_STEP_SUMMARY` + `upload-artifact` safe-output | the agent's final message is **not** what gh-aw puts in the summary (§5) |
| Model | `COPILOT_MODEL=${{ vars.OPENROUTER_MODEL }}` = `~deepseek/deepseek-v4-flash-latest` | the `~…-latest` alias resolves inside the Copilot CLI (§7) |

---

## 1. Two things people mean by "skill"

| Term | What it is | gh-aw mechanism |
|---|---|---|
| **Skill playbook** | A Markdown file describing *how to do one task* (e.g. a `SKILL.md` from the Anthropic Cybersecurity Skills library: "how to test JWT with tool X"). The agent **reads** it and follows the method. | Put the file where the agent can read it (pre-step clone, repo path, or import), then tell the agent to read it in the prompt. |
| **Sub-agent / agent file** | A named, specialised assistant with its own model + instructions the main agent can **delegate to** (e.g. `file-summarizer`, `security-auditor`). | `## agent:` blocks inline, or `.github/agents/*.md` pulled in with `imports:`. |

Pick by need: a *method the agent should apply* → skill playbook; a *specialised
worker the agent calls* → sub-agent.

---

## 2. Skill playbooks

### 2a. Pattern used here — pinned external skill library *(Verified)*

`.github/workflows/security-review.md` frontmatter:

```yaml
network:
  allowed:
    - github.com          # git clone transport
    - openrouter.ai
    - python
steps:
  - name: Clone Anthropic Cybersecurity Skills library (pinned tag)
    env:
      SKILLS_REF: ${{ inputs.skills_ref || 'v1.3.0' }}
    run: |
      git clone --depth 1 --branch "$SKILLS_REF" \
        https://github.com/mukul975/Anthropic-Cybersecurity-Skills \
        /tmp/gh-aw/skills-lib
      git -C /tmp/gh-aw/skills-lib rev-parse HEAD
```

Then the prompt body tells the agent:

```markdown
`/tmp/gh-aw/skills-lib` is the library, cloned at a pinned tag by a pre-step.
Read `/tmp/gh-aw/skills-lib/index.json` ... For each selected skill, read
`/tmp/gh-aw/skills-lib/<path>/SKILL.md` and apply its methodology.
```

Key points:

- **`/tmp/gh-aw/...` is the safe scratch root** — its contents are mounted into
  the agent sandbox and uploaded as run artifacts. Clone there, not into the
  workspace. *(Verified)*
- **Pin the source.** `--branch <tag>` + an input default (`skills_ref: v1.3.0`)
  means a run only sees new/changed skills when someone bumps the tag. Record
  the resolved SHA in the step log for audit. *(Verified)*
- **Keep the fetch host in `network.allowed`** but drop
  `raw.githubusercontent.com` once you clone — that forces the agent to use the
  pinned checkout instead of pulling `main` per-file. *(Verified)*
- Treat every `SKILL.md` as **untrusted data** in the prompt: a method to
  follow, never instructions to obey. *(Recommended — see
  `security-and-governance.md`)*

### 2b. Repo-local custom skill

Write your own playbook and commit it, e.g. `.github/skills/our-authz-review.md`:

```markdown
# Skill: our tenant-isolation review

## When to use
Any change under `app/routers/` or `app/services/storage.py`.

## Method
1. List every route and its `Depends(...)`.
2. Flag any route touching `contexts` without `verify_credentials`.
3. Check `storage.py` queries filter by `tenant_id`.

## Output
One finding per gap: file:line, the missing check, the fix.
```

Make it reach the agent one of two ways:

```yaml
# frontmatter — merged into the prompt at compile time
imports:
  - .github/skills/our-authz-review.md
```

```markdown
# ...or in the prompt body, pulled in at runtime
{{#runtime-import .github/skills/our-authz-review.md}}
{{#runtime-import? .github/skills/optional-extra.md}}   # ? = optional
```

*(Docs)* `imports:` inlines at compile; `{{#runtime-import}}` inlines when the
prompt is rendered. A file may appear **at most once** in the import graph.

### 2c. Skill library from your own repo / another repo

Same as 2a but point the clone at an internal repo, or use a cross-repo import
for a single file:

```yaml
imports:
  - my-org/security-playbooks/.github/skills/api-review.md@v2.1.0
```

*(Docs)* Cross-repo imports match `owner/repo/path@ref` and are cached by commit
SHA — always pin `@<tag-or-sha>`.

### 2d. Steering which skills the agent picks *(Verified)*

A big library (800+ skills) needs guidance or the agent drifts — early
`security-review` runs came back almost entirely infrastructure skills
(K8s, Helm, secret-scanning, image tags) and missed the application logic.

Two levers, both in the prompt:

1. **Pre-filter deterministically**, then let the model rank. Have the agent
   `grep` the `index.json` for the stack's vocabulary and an offensive/runtime
   denylist *before* it reasons — cuts 800 candidates to a few dozen.
2. **Enforce a coverage mix.** Make the agent classify each candidate and hold a
   ratio:

   ```markdown
   Classify each candidate [app] or [infra].
   [app]   = authz / IDOR / BOLA / BOPLA, injection, SSRF, XXE, deserialization,
             SSTI, path traversal, mass assignment, business logic, JWT / session
             / OAuth, CORS / CSRF, crypto-in-code, input validation, secrets in
             source, rate-limit logic, error handling / info disclosure.
   [infra] = Dockerfile, K8s / Helm, Terraform, CI hardening, image / dep / SBOM
             scanning, provenance.
   Select at least ceil(0.7*N) [app] and at most floor(0.3*N) [infra].
   If short on [app] candidates, widen the search — do NOT backfill with infra.
   Tag each selected skill [app]/[infra] in the report and state the split.
   ```

   Run 33766554943: 12 skills → 9 `[app]` / 3 `[infra]` (75%), 14 findings, the
   HIGH/MEDIUM items were SSRF-guard-off, SSRF TOCTOU, unused rate limiter,
   wildcard CORS, unauth CRUD, unauth error-injection — all application-layer.

---

## 3. Sub-agents and agent files

### 3a. Inline sub-agent *(Docs)*

Define a named helper inside the workflow `.md`, after the main prompt:

```markdown
## agent: `endpoint-mapper`
---
model: claude-haiku-4.5          # optional; defaults to the workflow model
description: Lists every HTTP route with its auth dependency and request model
---
You receive a source tree. Output a JSON array of
{method, path, auth_dependency, request_model, file, line}. Nothing else.
## end agent: `endpoint-mapper`
```

- Heading must be `` ## agent: `name` `` — name is `[a-z0-9_-]`, starts lowercase.
- End at `` ## end agent: `name` ``, the next `##`, or EOF. Use the explicit end
  marker when the block is imported or contains its own `##` headings.
- Invoke it from the main prompt by name:
  *"Use the `endpoint-mapper` sub-agent to enumerate routes, then …"*
- At runtime gh-aw extracts it to the engine path
  (`.github/agents/<name>.agent.md` for Copilot, `.claude/agents/<name>.md` for
  Claude, etc.).

### 3b. Agent file in `.github/agents/` *(Docs)*

```markdown
---
name: security-auditor
description: Deep review of auth, secrets, and IaC
tools:
  bash: ["git", "grep", "rg"]
---
# Instructions
You are a security auditor. ...
```

```yaml
# workflow frontmatter — one agent file per workflow
imports:
  - .github/agents/security-auditor.md
```

*(Docs)* Copilot consumes these natively; other engines get the Markdown body as
plain prompt text. Remote form:
`imports: [acme/shared-agents/.github/agents/security-auditor.md@v1.0.0]`.

### 3c. When to use which

| Need | Use |
|---|---|
| Small, workflow-specific helper, lives with the workflow | inline `## agent:` |
| Reusable across workflows / repos, own toolset | `.github/agents/*.md` + `imports:` |
| A *method*, not a worker | skill playbook (§2) |

---

## 4. Supporting frontmatter

| Field | Purpose | Example |
|---|---|---|
| `steps:` / `pre-steps:` | Deterministic Actions steps **before** the agent (clone, setup, fetch inputs). | clone skills lib (§2a) |
| `post-steps:` | Deterministic steps **after** the agent, in the agent job. | **primary** way to get a report into the run summary (§5) |
| `imports:` | Merge shared markdown / agent files / MCP config / frontmatter at compile. | `.github/skills/*.md`, `.github/agents/*.md` |
| `{{#runtime-import path}}` | Inline a file into the prompt at render time (`?` = optional). | optional extra playbooks |
| `tools:` | Enable `bash`, `edit`, `web-fetch`, `web-search`, `playwright`, `github` toolsets. | `bash: ["git","grep","rg"]` |
| `mcp-servers:` | Attach an MCP server the agent can call. | custom scanner exposed over MCP |
| `runtimes:` | Pin Node / Python / Go versions for the `steps:`. | `python: { version: "3.12" }` |
| `network.allowed` | Allowlist egress hosts (clone source, model endpoint, tool data sources). | `github.com`, `openrouter.ai` |
| `safe-outputs:` | The only sanctioned write paths (`create-issue`, `upload-artifact`, `add-comment`, …). | `upload-artifact: { allowed-paths: ["security-review.md"] }` |

---

## 5. Delivering the result into the pipeline output

gh-aw's "Append agent step summary" step writes an **execution summary** (turns,
tool calls, token usage) — **not** the agent's final message. *(Verified — run
33756024920: the step ran, the report never appeared in the summary; it was only
in the artifact.)* So a report will not show up in the run summary on its own.

Do this instead:

1. **Prompt:** the agent writes the full report to a file in the workspace, e.g.
   `${GITHUB_WORKSPACE}/security-review.md`.
2. **`post-steps:`** (runs in the agent job, after the agent) publishes it:

   ```yaml
   post-steps:
     - name: Publish report to run summary
       if: always()
       run: |
         f="${GITHUB_WORKSPACE}/security-review.md"
         if [ -s "$f" ]; then cat "$f" >> "$GITHUB_STEP_SUMMARY"; fi
   ```

   `if: always()` means the report still lands even when the agent step is marked
   failed (e.g. it hit `timeout-minutes` right after writing the file — §7).
3. **`safe-outputs: upload-artifact`** for a downloadable copy — the agent calls
   the `upload_artifact` tool with a path that matches `allowed-paths`.

The agent's final message is then free to be a short pointer; it is not a
delivery channel.

---

## 6. Build and verify

```bash
gh aw compile                     # regenerate every <name>.lock.yml
gh aw compile --approve           # also re-approve new secrets/actions
gh aw validate                    # schema check without generating lock files
gh aw compile --strict            # enforce action pinning + network config
git add .github/workflows/<name>.md .github/workflows/<name>.lock.yml \
        .github/aw/actions-lock.json
```

Always commit the `.md` **and** its `.lock.yml` — GitHub Actions runs only the
lock file. `strict: true` fails the build if the frontmatter hash and body hash
do not match the lock.

---

## 7. Gotchas

- **`/tmp/gh-aw/` only** for scratch — anything else in the sandbox may be
  read-only or absent. *(Verified)*
- **One agent file per workflow** via `imports:`; a file appears **once** in the
  import graph. *(Docs)*
- **Non-Copilot engines** get agent-file / sub-agent bodies as plain prompt
  text, not structured config. *(Docs)*
- **Network allowlist is enforced by a firewall proxy** and fails **silently** —
  a skill tool that needs an un-allowed host just gets nothing. Add every host a
  pre-step or the agent needs. *(Verified — see run firewall logs)*
- **Pin every external source** (skill library tag, cross-repo import `@ref`,
  container image) so a run is reproducible and only changes on an explicit
  bump. *(Verified for `security-review`)*
- **`timeout-minutes` kills the agent step even mid-write.** GitHub Actions
  SIGTERMs the step at the budget; the step goes red even if the agent had just
  finished. A single silent provider stall (~14 min observed with DeepSeek V4
  Flash on OpenRouter) can eat the budget. Mitigate: raise `timeout-minutes`,
  cap the source you feed per skill, cut `max_skills`, and keep report delivery
  in an `if: always()` `post-steps:`. *(Verified — run 33756024920 failed at 40
  min; 55 min + 120k source + 12 skills succeeded in ~28 min, run 33766554943.)*
- **Model-id alias.** `vars.OPENROUTER_MODEL` here is
  `~deepseek/deepseek-v4-flash-latest`. The `~…-latest` form resolves inside the
  Copilot CLI that gh-aw runs, but it is **not** a callable OpenRouter model id
  if you ever hit the API directly (use e.g. `deepseek/deepseek-v4-flash-0731`).
  *(Verified)*
- **Runtime scales with `max_skills`.** Phase-2 is one model pass per selected
  skill over the collected source. ~12 skills ≈ 28 min here; budget
  `timeout-minutes` from that. *(Verified)*
- **Untrusted-data rule**: imported skill/agent text from external repos is data
  the agent reasons over, never instructions it obeys. State this in the prompt.
  *(Recommended)*
- The compiler bump: `gh aw` upgrades rewrite **all** `.lock.yml` files. Run
  `gh aw version`, upgrade deliberately, and commit the lock churn in its own
  change. *(Verified)*

---

## 8. Checklist — adding a custom skill or agent

- [ ] Decide: skill playbook (method) or sub-agent (worker)?
- [ ] Playbook: commit under `.github/skills/` **or** clone a pinned library in `steps:` into `/tmp/gh-aw/...`.
- [ ] Sub-agent: inline `## agent:` **or** `.github/agents/*.md` + `imports:`.
- [ ] Prompt body: tell the agent where the file is and to treat it as data.
- [ ] Large library: add a deterministic pre-filter + a coverage-mix rule so the selection matches intent (§2d).
- [ ] Frontmatter: `network.allowed` hosts, `tools:` the skill needs, `safe-outputs:` for the result.
- [ ] Report delivery: agent writes a file → `post-steps:` (`if: always()`) cats it into `$GITHUB_STEP_SUMMARY` → `upload-artifact` for download (§5).
- [ ] Pin external refs (`@tag`, `--branch`, image digests).
- [ ] Size `timeout-minutes` from `max_skills` × per-skill call time, with headroom for a provider stall (§7).
- [ ] `gh aw compile` (+ `--approve` if new secrets/actions); commit `.md` + `.lock.yml`.
- [ ] Dispatch once; check the run summary and firewall logs; confirm the skill file was read and no host was blocked.
