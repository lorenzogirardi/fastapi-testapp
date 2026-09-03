#!/usr/bin/env python3
"""
run_review.py — on-demand static security review.

Reads a codebase, selects the relevant skills from the Anthropic Cybersecurity
Skills library (https://github.com/mukul975/Anthropic-Cybersecurity-Skills) with a
two-phase LLM pipeline, applies each selected skill to the source, and writes a
ranked findings report as Markdown.

No third-party dependencies — standard library only.

    python3 tools/run_review.py --project . --skills-dir ./skills-lib

Providers:
  openrouter (default)  needs OPENROUTER_API_KEY   (OPENROUTER_BASE_URL / OPENROUTER_MODEL optional)
  anthropic             needs ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SKILLS_REPO_URL = "https://github.com/mukul975/Anthropic-Cybersecurity-Skills"

DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash-0731"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"

# marker file (or glob) -> stack label
STACK_MARKERS: dict[str, str] = {
    "package.json": "node",
    "tsconfig.json": "node",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "Pipfile": "python",
    "setup.py": "python",
    "Gemfile": "ruby",
    "composer.json": "php",
    "Cargo.toml": "rust",
    "main.tf": "terraform",
    "versions.tf": "terraform",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "Chart.yaml": "kubernetes",
}
# any file under these dirs implies the label
STACK_DIR_MARKERS: dict[str, str] = {
    "kubernetes": "kubernetes",
    "k8s": "kubernetes",
    "helm": "kubernetes",
    ".github/workflows": "github-actions",
}

# per-stack keyword affinity for the offline fallback selector
SKILL_AFFINITY: dict[str, list[str]] = {
    "generic": [
        "injection", "authentication", "authorization", "access control",
        "secret", "hardcoded", "cryptograph", "input validation", "owasp",
        "api-security", "api security", "ssrf", "xss", "csrf", "cors",
        "deserialization", "path traversal", "open redirect", "rate limit",
        "session", "jwt", "oauth", "supply chain", "dependency",
    ],
    "python": ["python", "pip", "django", "flask", "fastapi", "pyyaml",
               "pickle", "jinja", "sqlalchemy", "asgi", "wsgi"],
    "node": ["javascript", "typescript", "npm", "node.js", "express",
             "prototype pollution", "nestjs", "next.js"],
    "go": ["golang", "gosec", "net/http"],
    "java": ["java", "spring", "maven", "gradle", "xxe", "log4j", "jackson"],
    "ruby": ["ruby", "rails", "brakeman"],
    "php": ["php", "laravel", "symfony", "composer"],
    "rust": ["rust", "cargo"],
    "docker": ["docker", "dockerfile", "container image", "oci image", "base image"],
    "kubernetes": ["kubernetes", "k8s", "kube-", "kubelet", "helm", "rbac",
                   "pod security", "admission controller", "service account"],
    "terraform": ["terraform", "infrastructure as code", "iac", "hcl", "tfstate"],
    "github-actions": ["github actions", "workflow", "ci/cd pipeline",
                       "pipeline", "oidc", "self-hosted runner"],
}

# skills matching these need a live target / dump / agent — penalise for static review
RUNTIME_ONLY_KEYWORDS: list[str] = [
    "forensic", "memory-dump", "memory dump", "cobalt-strike", "cobalt strike",
    "falco", "container-escape", "container escape", "mimikatz",
    "active-directory", "active directory", "ghidra", "malware-analys",
    "malware analys", "post-exploit", "post exploit", "lateral-movement",
    "lateral movement", "gcp", "google-workspace", "google workspace",
    "office365", "office 365", "azure-ad", "azure ad", "volatility",
    "pcap", "wireshark", "yara", "edr", "siem", "incident response",
]

SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rb", ".php",
    ".java", ".kt", ".rs", ".c", ".h", ".cpp", ".cc", ".cs", ".sh", ".bash",
    ".sql", ".tf", ".hcl", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
    ".env", ".dockerfile", ".conf", ".template", ".tpl",
}
SOURCE_EXTRA_NAMES = {"Dockerfile", "Makefile", ".env", ".env.example"}
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", "target",
    "vendor", ".terraform", "coverage", "htmlcov", ".idea", ".vscode",
    "site-packages",
}

SRC_SNIPPET_CAP = 40_000      # chars of source sent in phase 1
SOURCE_CAP = 200_000          # chars of source sent per phase-2 call
SKILL_MD_CAP = 14_000         # chars of a SKILL.md sent per phase-2 call
PHASE2_MAX_TOKENS = 2_000
SEVERITY_ORDER = ["HIGH", "MEDIUM", "LOW", "INFO"]


# --------------------------------------------------------------------------- #
# Project inspection
# --------------------------------------------------------------------------- #

def detect_stack(project: Path) -> list[str]:
    stacks = {"generic"}
    for marker, label in STACK_MARKERS.items():
        if (project / marker).exists():
            stacks.add(label)
    for rel, label in STACK_DIR_MARKERS.items():
        if (project / rel).is_dir():
            stacks.add(label)
    # nested markers (monorepos) — shallow walk
    for path in project.rglob("*"):
        if _skip(path, project):
            continue
        if path.name in STACK_MARKERS:
            stacks.add(STACK_MARKERS[path.name])
    return sorted(stacks)


def _skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in SKIP_DIRS for part in rel_parts)


def _is_source(path: Path) -> bool:
    if path.name in SOURCE_EXTRA_NAMES:
        return True
    return path.suffix.lower() in SOURCE_EXTS


def build_file_tree(project: Path, max_entries: int = 500) -> str:
    entries: list[str] = []
    for path in sorted(project.rglob("*")):
        if path.is_dir() or _skip(path, project):
            continue
        entries.append(str(path.relative_to(project)))
        if len(entries) >= max_entries:
            entries.append("... (truncated)")
            break
    return "\n".join(entries)


def collect_source(project: Path, cap: int) -> str:
    chunks: list[str] = []
    total = 0
    files = sorted(
        (p for p in project.rglob("*")
         if p.is_file() and not _skip(p, project) and _is_source(p)),
        key=lambda p: (len(p.relative_to(project).parts), str(p)),
    )
    for path in files:
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(project)
        header = f"\n# ==================== {rel} ====================\n"
        budget = cap - total
        if budget <= len(header):
            break
        body = text[: budget - len(header)]
        chunks.append(header + body)
        total += len(header) + len(body)
        if total >= cap:
            break
    return "".join(chunks)


# --------------------------------------------------------------------------- #
# Skills library
# --------------------------------------------------------------------------- #

def ensure_skills_dir(skills_dir: str | None) -> Path:
    if skills_dir:
        p = Path(skills_dir).expanduser().resolve()
        if not (p / "skills").is_dir() and not (p / "index.json").is_file():
            sys.exit(f"--skills-dir {p} does not look like the skills library")
        return p
    dest = Path(tempfile.mkdtemp(prefix="cyber-skills-"))
    print(f"[skills] cloning {SKILLS_REPO_URL} -> {dest}", file=sys.stderr)
    subprocess.run(
        ["git", "clone", "--depth", "1", SKILLS_REPO_URL, str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return dest


_FM_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.M)
_FM_DESC = re.compile(r"description:\s*(?:>-|\|)?\s*\n?(.*?)(?:\n[a-z_]+:|\n---)", re.S)


def load_skills(lib: Path) -> list[dict]:
    idx = lib / "index.json"
    if idx.is_file():
        data = json.loads(idx.read_text("utf-8"))
        out = []
        for s in data.get("skills", []):
            out.append({
                "name": s["name"],
                "description": " ".join(s.get("description", "").split()),
                "path": s.get("path", f"skills/{s['name']}"),
            })
        if out:
            return out
    out = []
    for skill_md in sorted((lib / "skills").glob("*/SKILL.md")):
        text = skill_md.read_text("utf-8", errors="replace")
        fm = text.split("---", 2)
        head = fm[1] if len(fm) >= 3 else text[:2000]
        name_m = _FM_NAME.search(head)
        desc_m = _FM_DESC.search(head)
        name = name_m.group(1).strip() if name_m else skill_md.parent.name
        desc = " ".join((desc_m.group(1) if desc_m else "").split())
        out.append({
            "name": name,
            "description": desc,
            "path": f"skills/{skill_md.parent.name}",
        })
    return out


def read_skill_md(lib: Path, skill_path: str) -> str:
    p = lib / skill_path / "SKILL.md"
    if not p.is_file():
        p = lib / "skills" / skill_path.split("/")[-1] / "SKILL.md"
    return p.read_text("utf-8", errors="replace") if p.is_file() else ""


# --------------------------------------------------------------------------- #
# Skill selection
# --------------------------------------------------------------------------- #

def select_skills_keyword(skills: list[dict], stacks: list[str],
                          max_skills: int) -> list[dict]:
    affinity: list[str] = []
    for stack in stacks:
        affinity += SKILL_AFFINITY.get(stack, [])
    affinity = list(dict.fromkeys(affinity))  # de-dupe, keep order

    scored: list[tuple[int, dict]] = []
    for skill in skills:
        text = f"{skill['name']} {skill['description']}".lower()
        pos = sum(1 for kw in affinity if kw in text)
        neg = sum(1 for kw in RUNTIME_ONLY_KEYWORDS if kw in text)
        score = max(0, pos - 2 * neg)
        if score > 0:
            scored.append((score, skill))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [s for _, s in scored[:max_skills]]


def select_skills_llm(client: "LLMClient", skills: list[dict], stacks: list[str],
                      file_tree: str, source_snippet: str,
                      max_skills: int) -> list[dict]:
    by_name = {s["name"]: s for s in skills}
    catalog = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
    system = (
        "You are a security lead choosing which static-analysis security skills "
        "apply to a specific codebase. Consider the languages, frameworks, "
        "entry points, and exposure surface you can see. Prefer skills that can "
        "be applied by reading source code. Exclude skills that need a running "
        "target, a memory dump, or a live agent. "
        f"Return ONLY a JSON array of at most {max_skills} skill name strings, "
        "most relevant first. No prose, no markdown."
    )
    user = (
        f"Detected stacks: {', '.join(stacks)}\n\n"
        f"FILE TREE:\n{file_tree}\n\n"
        f"SOURCE SAMPLE:\n{source_snippet}\n\n"
        f"AVAILABLE SKILLS:\n{catalog}"
    )
    raw = client.complete(system, user, max_tokens=1_500)
    names = _extract_json(raw)
    picked: list[dict] = []
    if isinstance(names, list):
        for n in names:
            if isinstance(n, str) and n in by_name and by_name[n] not in picked:
                picked.append(by_name[n])
    return picked[:max_skills]


# --------------------------------------------------------------------------- #
# Skill application (phase 2)
# --------------------------------------------------------------------------- #

REVIEW_SYSTEM = (
    "You are a senior application security engineer doing a static code review. "
    "Apply the methodology described in the provided SKILL to the provided "
    "SOURCE. Report ONLY concrete findings that are actually present in this "
    "source, each anchored to a real file path from the source. Do not speculate "
    "and do not invent paths. If the skill does not apply or you find nothing, "
    "return an empty array.\n\n"
    "Return ONLY a JSON array. Each element:\n"
    '{"severity":"HIGH|MEDIUM|LOW|INFO","title":"<=60 chars",'
    '"file":"relative/path or N/A","line":<int or null>,'
    '"detail":"what and why, 1-3 sentences","fix":"one sentence"}'
)


def review_skill(client: "LLMClient", skill: dict, skill_md: str,
                 source: str) -> list[dict]:
    user = (
        f"SKILL ({skill['name']}):\n{skill_md[:SKILL_MD_CAP]}\n\n"
        f"SOURCE:\n{source[:SOURCE_CAP]}"
    )
    raw = client.complete(REVIEW_SYSTEM, user, max_tokens=PHASE2_MAX_TOKENS)
    data = _extract_json(raw)
    findings: list[dict] = []
    if isinstance(data, list):
        for f in data:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity", "INFO")).upper()
            if sev not in SEVERITY_ORDER:
                sev = "INFO"
            findings.append({
                "severity": sev,
                "title": str(f.get("title", "untitled"))[:120],
                "file": str(f.get("file", "N/A")),
                "line": f.get("line") if isinstance(f.get("line"), int) else None,
                "detail": str(f.get("detail", "")).strip(),
                "fix": str(f.get("fix", "")).strip(),
                "skill": skill["name"],
            })
    return findings


# --------------------------------------------------------------------------- #
# LLM client
# --------------------------------------------------------------------------- #

class LLMClient:
    def __init__(self, provider: str, model: str, base_url: str, api_key: str):
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        if self.provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        else:  # openrouter / any OpenAI-compatible endpoint
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        body = json.dumps(payload).encode()
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, data=body, headers=headers,
                                             method="POST")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read())
                if self.provider == "anthropic":
                    return "".join(
                        blk.get("text", "") for blk in data.get("content", [])
                    )
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode(errors="replace")[:500]
                raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM call failed after retries: {last_err}")


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text: str):
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def render_report(project_name: str, provider: str, model: str,
                  smart: bool, skills: list[dict], findings: list[dict],
                  duration: float) -> str:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f["severity"]] += 1
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]),
                                 f["file"], f["line"] or 0))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L = [
        f"# Security Review: {project_name}",
        "",
        f"**Date:** {now}",
        f"**Provider:** {provider} / {model}",
        f"**Skill selection:** {'LLM-based (Phase 1)' if smart else 'keyword fallback'}",
        f"**Skills evaluated:** {len(skills)}  ·  **Scan duration:** {duration:.1f}s",
        "",
        "## Summary",
        "",
        "| " + " | ".join(SEVERITY_ORDER) + " |",
        "|" + "|".join(["---"] * len(SEVERITY_ORDER)) + "|",
        "| " + " | ".join(str(counts[s]) for s in SEVERITY_ORDER) + " |",
        "",
    ]
    if not findings:
        L += ["No findings. Every selected skill returned an empty result.", ""]
    for sev in SEVERITY_ORDER:
        group = [f for f in findings if f["severity"] == sev]
        if not group:
            continue
        L.append(f"## {sev}")
        L.append("")
        for f in group:
            loc = f["file"] + (f":{f['line']}" if f["line"] else "")
            L += [
                f"### {f['title']}",
                "",
                f"- **Location:** `{loc}`",
                f"- **Skill:** `{f['skill']}`",
                f"- **Detail:** {f['detail']}",
                f"- **Fix:** {f['fix']}",
                "",
            ]
    L += ["## Skills applied", ""]
    L += [f"- `{s['name']}`" for s in skills]
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def resolve_client(args) -> LLMClient:
    if args.provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            sys.exit("ANTHROPIC_API_KEY not set")
        model = args.model or DEFAULT_ANTHROPIC_MODEL
        return LLMClient("anthropic", model, "", key)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")
    base = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE)
    env_model = os.environ.get("OPENROUTER_MODEL", "")
    # gh-aw / Copilot alias syntax ("~vendor/model-latest") is not a callable
    # OpenRouter model id — ignore it and fall back to a pinned slug.
    if env_model.startswith("~") or env_model.endswith("-latest"):
        env_model = ""
    model = args.model or env_model or DEFAULT_OPENROUTER_MODEL
    return LLMClient("openrouter", model, base, key)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=".", help="path to the repo under review")
    ap.add_argument("--skills-dir", default=None,
                    help="path to the skills library (cloned automatically if omitted)")
    ap.add_argument("--provider", choices=["openrouter", "anthropic"],
                    default="openrouter")
    ap.add_argument("--model", default=None, help="override model id")
    ap.add_argument("--max-skills", type=int, default=50)
    ap.add_argument("--no-smart-select", action="store_true",
                    help="skip the phase-1 LLM call, use keyword scoring only")
    ap.add_argument("--output", default=None,
                    help="report path (default: SECURITY-REVIEW-<ts>.md in project root)")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        sys.exit(f"--project {project} is not a directory")

    client = resolve_client(args)
    lib = ensure_skills_dir(args.skills_dir)
    skills = load_skills(lib)
    if not skills:
        sys.exit("no skills found in the library")
    print(f"[skills] {len(skills)} skills in library", file=sys.stderr)

    stacks = detect_stack(project)
    print(f"[stack]  {', '.join(stacks)}", file=sys.stderr)

    file_tree = build_file_tree(project)
    source = collect_source(project, SOURCE_CAP)
    if not source.strip():
        sys.exit("no source files collected from the project")

    t0 = time.time()
    smart = not args.no_smart_select
    selected: list[dict] = []
    if smart:
        try:
            selected = select_skills_llm(
                client, skills, stacks, file_tree,
                source[:SRC_SNIPPET_CAP], args.max_skills,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[phase1] LLM selection failed ({e}); using keyword fallback",
                  file=sys.stderr)
            smart = False
    if not selected:
        smart = False
        selected = select_skills_keyword(skills, stacks, args.max_skills)
    if not selected:
        sys.exit("no skills selected for this project")
    print(f"[select] {len(selected)} skills "
          f"({'LLM' if smart else 'keyword'})", file=sys.stderr)

    all_findings: list[dict] = []
    for i, skill in enumerate(selected, 1):
        md = read_skill_md(lib, skill["path"])
        if not md:
            print(f"[{i}/{len(selected)}] {skill['name']}: SKILL.md missing, skip",
                  file=sys.stderr)
            continue
        try:
            fs = review_skill(client, skill, md, source)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(selected)}] {skill['name']}: error {e}",
                  file=sys.stderr)
            continue
        all_findings += fs
        print(f"[{i}/{len(selected)}] {skill['name']}: {len(fs)} finding(s)",
              file=sys.stderr)

    duration = time.time() - t0
    report = render_report(project.name, client.provider, client.model,
                           smart, selected, all_findings, duration)

    out = Path(args.output) if args.output else (
        project / f"SECURITY-REVIEW-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md"
    )
    out.write_text(report, "utf-8")
    print(f"\n[done] {len(all_findings)} finding(s) -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
