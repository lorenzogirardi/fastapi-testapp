# Glossary

Terms used in this documentation. Plain-language definitions for mixed
technical/non-technical audiences.

**Agentic Workflow** — A GitHub Actions workflow that runs an AI "agent" to perform
software-engineering tasks (review, diagnosis, implementation) with some autonomy,
but under human-defined constraints.

**GitHub Agentic Workflows (`gh-aw`)** — GitHub's extension for authoring agentic
workflows in Markdown. Compiler `v0.87.10` in this repo.

**`.md` workflow source** — The human-authored Markdown file (prompt + YAML frontmatter)
that defines an agentic workflow, e.g. `ai-pr-review.md`.

**`.lock.yml` (generated)** — The compiled GitHub Actions YAML produced by `gh aw compile`
from a `.md` source. Checked in; do not edit by hand.

**AWF (Agent Workflow Firewall)** — The security enclave gh-aw runs on the Actions
runner: egress filtering (Squid), LLM interception (api-proxy), and tool exposure
(MCP gateway).

**api-proxy** — AWF sidecar that intercepts the agent's LLM calls and routes them to
the configured provider (here, OpenRouter). Holds provider secrets.

**MCP gateway (mcpg)** — Exposes GitHub API capabilities to the agent as "tools"
(repo read, PR comment, etc.) inside the enclave.

**Safe output** — A gated, platform-controlled action an agent may request (post
comment, update PR, open PR). Applied by gh-aw, not by the agent directly.

**BYOK (Bring Your Own Key)** — Using your own credentials/endpoint for the LLM
provider. Here, `COPILOT_PROVIDER_*` env vars route Copilot-engine calls to OpenRouter.

**Copilot engine (`engine: copilot`)** — The agent harness used by gh-aw in this repo.
Despite the name, the actual model is `~deepseek/deepseek-v4-flash-latest` served by OpenRouter, reached via
Copilot's BYOK provider hook.

**OpenRouter** — The external LLM provider (`https://openrouter.ai/api/v1`) that
actually generates the agent's responses. The base URL is set from the repo
variable `OPENROUTER_BASE_URL`, not hardcoded in the workflows.

**`~deepseek/deepseek-v4-flash-latest`** — The model identifier sent to OpenRouter
for these workflows, set via `COPILOT_MODEL=${{ vars.OPENROUTER_MODEL }}` in
`engine.env` (bare provider id, no `copilot/` prefix). The leading `~` is an
OpenRouter router alias that resolves to the current DeepSeek V4 Flash snapshot.
Not a free tier — calls cost tokens.

**Wire API (`COPILOT_PROVIDER_WIRE_API`)** — The request/response format the engine
uses to talk to the provider. Set to `completions` (OpenAI-compatible) here.

**`OPENROUTER_API_KEY`** — Repository secret holding the OpenRouter credential
(`sk-or-v1-…`, from <https://openrouter.ai/keys>); injected as
`COPILOT_PROVIDER_API_KEY`. Name only; value not shown in docs.

**`OPENROUTER_BASE_URL`** — Repository variable holding the provider base URL
(`https://openrouter.ai/api/v1`); injected as `COPILOT_PROVIDER_BASE_URL`. A
variable, not a secret, so the endpoint can be changed without touching the
workflows.

**`OPENROUTER_MODEL`** — Repository variable holding the model id
(`~deepseek/deepseek-v4-flash-latest`); injected as `COPILOT_MODEL`. Change the
model without recompiling the workflows.

**`network.allowed`** — Egress allowlist for the AWF enclave (e.g. `github.com`,
`openrouter.ai`). Limits where the agent can send requests.

**`threat-detection`** — An optional gh-aw sub-agent that scans agent output for
threats. Disabled (`false`) in this repo's workflows; see security doc.

**`default-ai-credits-pricing`** — Fallback token price used by the proxy when a model
is unknown; required here or the proxy rejects the BYOK model (HTTP 400). The
`{input: 3.0, output: 15.0}` values are a conservative fallback estimate, not
DeepSeek V4 Flash's real rate (which is lower).

**Prompt injection** — An attempt to make the agent follow instructions hidden in
untrusted content (PR text, code, comments). Mitigated by declaring such content
"untrusted" in prompts.

**Protected paths** — Files the write workflows are told never to modify
(`.github/`, `kubernetes/`, `helm/`, `Dockerfile`, `pyproject.toml`, `tests/`,
`requirements.txt`).

**Concurrency / `cancel-in-progress`** — GitHub setting that cancels a stale run when
the same PR/issue is re-triggered, preventing duplicate runs.

**Human-in-the-loop** — Design where a person must review and approve before any
consequential action (merge, release). This system is strictly human-in-the-loop.

**SSRF (Server-Side Request Forgery)** — An attack where the server is tricked into
fetching internal/forbidden URLs. Relevant to PR #8's proxy endpoint.

**Run / Job / Step** — GitHub Actions hierarchy: a *run* is one workflow execution
(e.g. `33270177709`); a *job* is a stage (`agent`, `safe_outputs`); a *step* is a unit
of work.

**Artifact** — Files saved by a run for later inspection (e.g. `agent-stdio.log`,
`awf-config.json`).
