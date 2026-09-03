# Adapting custom skills and agents to a gh-aw workflow

How to plug **skill playbooks** and **custom / sub-agents** into a GitHub
Agentic Workflow (`github/gh-aw`), using the `security-review` workflow in this
repo as the worked example.

**Confidence legend:** *(Verified)* observed in this repo's workflow files / run
metadata; *(Docs)* from `github.github.io/gh-aw`; *(Recommended)* guidance.

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
| `post-steps:` | Deterministic steps **after** the agent (e.g. `cat report.md >> "$GITHUB_STEP_SUMMARY"`). | belt-and-suspenders report delivery |
| `imports:` | Merge shared markdown / agent files / MCP config / frontmatter at compile. | `.github/skills/*.md`, `.github/agents/*.md` |
| `{{#runtime-import path}}` | Inline a file into the prompt at render time (`?` = optional). | optional extra playbooks |
| `tools:` | Enable `bash`, `edit`, `web-fetch`, `web-search`, `playwright`, `github` toolsets. | `bash: ["git","grep","rg"]` |
| `mcp-servers:` | Attach an MCP server the agent can call. | custom scanner exposed over MCP |
| `runtimes:` | Pin Node / Python / Go versions for the `steps:`. | `python: { version: "3.12" }` |
| `network.allowed` | Allowlist egress hosts (clone source, model endpoint, tool data sources). | `github.com`, `openrouter.ai` |
| `safe-outputs:` | The only sanctioned write paths (`create-issue`, `upload-artifact`, `add-comment`, …). | `upload-artifact: { allowed-paths: ["security-review.md"] }` |

---

## 5. Delivering the result

The agent's **final message is copied verbatim into the GitHub Actions run
summary**. *(Verified)* So:

- To put a report in the pipeline output, make the agent's last message **be**
  the report (raw Markdown, unfenced) — not a "written to file" sentence.
- For a downloadable copy, also write the file and call the `upload_artifact`
  safe-output tool with an `allowed-paths` entry matching the filename.
- A `post-steps:` `cat <file> >> "$GITHUB_STEP_SUMMARY"` is a reliable fallback
  if the model keeps summarising instead of emitting the full report.

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
- [ ] Frontmatter: `network.allowed` hosts, `tools:` the skill needs, `safe-outputs:` for the result.
- [ ] Pin external refs (`@tag`, `--branch`, image digests).
- [ ] `gh aw compile` (+ `--approve` if new secrets/actions); commit `.md` + `.lock.yml`.
- [ ] Dispatch once; check the run summary and firewall logs; confirm the skill file was read and no host was blocked.
