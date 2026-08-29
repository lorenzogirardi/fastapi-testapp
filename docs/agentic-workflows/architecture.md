# Architecture

Technical reference for the agentic workflow system implemented in this repository
using GitHub Agentic Workflows (`gh-aw`, compiler `v0.86.2`).

**Confidence legend:** *(Verified)* = directly observed in checked-in files or
run metadata; *(Inferred)* = reasonable deduction from gh-aw behavior/manifest;
*(Recommended)* = suggested future control; *(Unknown)* = not available.

## Components and sources (verified)

| Component | Evidence |
|---|---|
| Agentic workflow sources | `.github/workflows/ai-pr-review.md`, `ai-ci-diagnose.md`, `ai-fix-pr.md`, `ai-issue-to-draft-pr.md` |
| Generated GitHub Actions workflows | `.github/workflows/*.lock.yml` (header: `gh-aw (v0.86.2)`, `agent_id: copilot`, `agent_model: copilot/hy3-free`) |
| gh-aw setup action | `github/gh-aw-actions/setup@6aab9e5b…` (v0.86.2) per lock manifest |
| Firewall images | `ghcr.io/github/gh-aw-firewall/{agent,api-proxy,squid}:0.27.44`, `gh-aw-mcpg:v0.4.9`, `github-mcp-server:v1.9.0` |
| LLM routing | `engine.env`: `COPILOT_PROVIDER_BASE_URL=https://opencode.ai/zen/v1`, `COPILOT_PROVIDER_WIRE_API=completions`, `COPILOT_PROVIDER_API_KEY=${{ secrets.OPENCODE_API_KEY }}` (verified in every workflow `.md`) |
| Secrets in manifest | `OPENCODE_API_KEY`, `COPILOT_GITHUB_TOKEN`, `GH_AW_GITHUB_TOKEN`, `GH_AW_GITHUB_MCP_SERVER_TOKEN`, `GITHUB_TOKEN` |

## How gh-aw works here (verified + inferred)

1. Authors edit Markdown workflow files (`.md`). A CI/build step runs
   `gh aw compile`, producing checked-in `.lock.yml` GitHub Actions workflows.
   *(Verified: lock header says "To update this file, edit the corresponding .md
   file and run: gh aw compile".)*
2. On a trigger (e.g., `pull_request`), GitHub runs the compiled workflow.
3. gh-aw spins up an **Agent Workflow Firewall (AWF)** enclave: a Squid proxy
   (egress filtering), an **api-proxy** sidecar (LLM traffic interception), and
   an **MCP gateway** (exposes GitHub tools to the agent).
   *(Inferred from manifest container list and standard gh-aw architecture.)*
4. The **Copilot engine harness** (`engine: copilot`) launches the coding-agent
   CLI inside the agent container. All LLM requests go through the api-proxy.
5. The api-proxy is configured (via `COPILOT_PROVIDER_BASE_URL`) to forward
   those requests to **OpenCode Zen** (`https://opencode.ai/zen/v1`) using the
   OpenAI `completions` wire format, authenticated with `OPENCODE_API_KEY`.
   *(Verified: proxy target `opencode.ai` and `copilotProviderBaseUrl` observed
   in a prior run's `awf-config.json`; earlier proxy error confirmed
   `WIRE_API=completions`.)*
6. The model that actually generates the response is **`hy3-free`** on OpenCode
   Zen, even though the harness is the Copilot CLI. *(Verified: `model:
   copilot/hy3-free` in every workflow; the model segment `hy3-free` is sent to
   Zen.)*
7. Agent output is captured, and any **safe output** (e.g., a PR comment) is
   applied by a gated `safe_outputs` job, not by the agent directly.

## System context diagram

```mermaid
flowchart TB
  User[Product / Engineer] -->|opens PR / issue, reviews| GH[GitHub Issue / PR]
  GH -->|event: pull_request / workflow_dispatch| WF[gh-aw workflow .md -> .lock.yml]
  WF --> Runner[GitHub Actions Runner + AWF enclave]
  Runner --> Agent[coding agent: copilot harness]
  Agent -->|LLM calls via api-proxy| Zen[OpenCode Zen: model hy3-free]
  Agent -->|tool calls via MCP gateway| GH
  Runner -->|safe output: comment / PR| GH
  GH -->|notification| Reviewer[Human reviewer / maintainer]
  Reviewer -->|approve / merge| Merge[(Merge decision)]
  Runner -.->|logs, artifacts| Audit[Audit trail: run logs, PR timeline]
```

## Component / data-flow diagram

```mermaid
flowchart LR
  subgraph TrustedControl[Trusted control inputs]
    WFsrc[workflow .md + prompt]
    Policy[safe-outputs, permissions, network.allowed]
    Vars[repo variables / secrets references]
  end
  subgraph Untrusted[Untrusted repository/event content]
    PRtext[PR title/body/comments]
    Diff[code diff]
    Logs[CI logs]
    Code[source files]
  end
  subgraph Secrets[Secret boundary - never in agent]
    SEC[OPENCODE_API_KEY, GITHUB_TOKEN, *MCP* tokens]
  end
  subgraph Runner[AWF enclave on runner]
    Squid[Squid egress filter]
    Proxy[api-proxy LLM interceptor]
    MCP[MCP gateway: GitHub tools]
    AgentC[agent container]
  end
  subgraph Provider[LLM provider boundary]
    Zen[OpenCode Zen / hy3-free]
  end

  WFsrc --> AgentC
  PRtext --> AgentC
  Diff --> AgentC
  Logs --> AgentC
  Code --> AgentC
  Policy --> AgentC
  AgentC -->|LLM request| Proxy -->|routed to| Zen
  SEC -.injected by sidecar.-> Proxy
  AgentC -->|tool call| MCP -->|GitHub API| GH[(GitHub)]
  AgentC -->|proposed output| SafeOut[safe_outputs gate]
  SafeOut -->|comment / PR| GH
  Squid -.filters egress.-> Zen
```

## Workflow lifecycle sequence

```mermaid
sequenceDiagram
  participant U as Author/Requestor
  participant GH as GitHub
  participant WF as gh-aw workflow
  participant PA as pre_activation
  participant AC as activation
  participant AG as agent job
  participant Z as OpenCode Zen (hy3-free)
  participant SO as safe_outputs
  participant R as Human reviewer

  U->>GH: open PR / dispatch
  GH->>WF: trigger event
  WF->>PA: auth + guardrails + budget
  PA->>AC: build prompt + context (diff, repo)
  AC->>AG: start agent container + firewall
  AG->>Z: LLM calls (via api-proxy)
  Z-->>AG: model response
  AG->>SO: requested safe output (comment)
  SO->>GH: post PR comment
  GH->>R: notify
  R->>GH: review + merge/reject (human)
```

## Trust-boundary diagram

```mermaid
flowchart TB
  subgraph Ext[External / untrusted]
    Fork[External contributor / fork PR content]
    Zen[OpenCode Zen provider]
  end
  subgraph GH[GitHub platform]
    Repo[(Repository code + secrets)]
    Token[GITHUB_TOKEN boundary]
    Branch[Protected branch / merge boundary]
  end
  subgraph Run[Runner + AWF enclave]
    Agent[agent container - untrusted content executed/read]
    Proxy[api-proxy - secret boundary]
    Squid[Squid - network boundary]
  end

  Fork -.untrusted content.-> Agent
  Agent -.reads.-> Repo
  Proxy -.holds secrets, not the agent.- Agent
  Agent -->|LLM| Proxy -->|egress filtered| Zen
  Proxy -.OPENCODE_API_KEY.-> Zen
  Agent -.tool calls.-> Repo
  Repo -.GITHUB_TOKEN.-> Token
  Token -.cannot write protected paths.-> Branch
  Branch -.merge decision.-> Human[(Human)]
```

## Repository context supplied to the agent

- PR/issue text, comments, and the code diff *(verified: prompt instructs review of
  "PR title, body, comments, diff, and all repository files")*.
- A checked-out copy of the repository at the PR head *(inferred: standard
  `actions/checkout` in the generated workflow)*.
- Suggested deterministic commands: `pytest tests/ -m "not integration" -q` and
  `flake8 . --count --select=E9,F63,F7,F82` *(verified in prompt)*.

## Agent output mechanisms

| Output | Mechanism | Evidence |
|---|---|---|
| PR comment | `safe-outputs: add-comment: null` | `ai-pr-review.md:34` |
| PR update | `safe-outputs: update-pull-request: null` | `ai-fix-pr.md:39` |
| Draft PR | `safe-outputs: create-pull-request: null` | `ai-issue-to-draft-pr.md:33` |

## Deterministic quality gates (independent of the agent)

- Unit tests: `pytest tests/ -m "not integration"` *(verified command in prompts)*.
- Lint: `flake8` selective codes *(verified)*.
- Branch protection and human approval remain the merge gate *(verified: workflows
  request only read scopes; no auto-merge step exists)*.

## Observability / audit sources

- GitHub Actions run logs and job timeline (run `33270177709` for PR #8).
- Artifacts: `agent` (includes `agent-stdio.log`, `awf-config.json`, proxy logs).
- PR timeline and the posted AI comment.
- Git history (`git log`) for the workflow and application changes.
