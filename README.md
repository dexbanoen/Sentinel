# Agent — Local LLM-Powered PR Reviewer

**Agent** is a GitHub Actions bot that reviews pull requests using a local [Ollama](https://ollama.com) LLM running on a self-hosted runner.

> ⚠️ Agent **never merges code**. It only posts reviews (APPROVE / COMMENT / REQUEST\_CHANGES).

---

## How it works

```
PR opened / updated
        │
        ▼
┌─────────────────────────┐
│  Fetch PR diff (GitHub) │
└────────────┬────────────┘
             │
             ▼
┌──────────────────────────────────────┐
│  Run basic checks                    │
│   • Tests  (pytest / npm test)       │
│   • Lint   (ruff / flake8 / eslint)  │
│   • Types  (mypy / tsc)              │
└────────────┬─────────────────────────┘
             │
             ▼
┌───────────────────────────────┐
│  Send diff + check results    │
│  to local Ollama model        │
│  → returns structured JSON   │
└────────────┬──────────────────┘
             │
             ▼
┌──────────────────────────────────────────┐
│  Apply safety rules                      │
│  • Fail? → REQUEST_CHANGES               │
│  • Critical/High issue? → REQUEST_CHANGES│
│  • LLM parse error? → safe COMMENT only  │
│  • All clear? → honour LLM verdict       │
└────────────┬─────────────────────────────┘
             │
             ▼
   Submit GitHub PR Review
```

---

## Requirements

| Requirement | Notes |
|---|---|
| GitHub self-hosted runner | Needs network access to `localhost:11434` |
| [Ollama](https://ollama.com) | Running on the same machine as the runner |
| Python 3.11+ | Set up by the workflow automatically |
| An Ollama model | Default: `codellama:13b` — change via `OLLAMA_MODEL` variable |

---

## Setup

### 1. Add a self-hosted runner to your repository

Follow [GitHub's guide](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).

### 2. Install and start Ollama on the runner machine

```bash
# Install Ollama (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull your chosen model
ollama pull codellama:13b

# Start the server (it runs on port 11434 by default)
ollama serve
```

### 3. Push this repository to GitHub

The workflow at `.github/workflows/agent-review.yml` will trigger automatically on every pull request.

### 4. (Optional) Configure via repository variables

Go to **Settings → Secrets and variables → Actions → Variables** and set any of:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | URL of the Ollama server |
| `OLLAMA_MODEL` | `codellama:13b` | Model to use for reviews |
| `MAX_DIFF_CHARS` | `20000` | Max diff size before skipping LLM review |

---

## File layout

```
.github/
  workflows/
    agent-review.yml          ← GitHub Actions workflow

tools/
  agent_reviewer/
    main.py                   ← Entry point / orchestrator
    github_client.py          ← GitHub REST API wrapper
    checks.py                 ← pytest / lint / typecheck runner
    llm.py                    ← Ollama API client
    prompts.py                ← System + user prompt templates
    reviewer.py               ← Decision logic + review body formatting
    requirements.txt          ← PyGithub, requests
```

---

## Safety guarantees

- **No merge calls** — `github_client.py` has no merge method; the workflow's `permissions` block grants only `pull-requests: write` and `contents: read`.
- **No approve on failed checks** — `reviewer.py` hard-overrides any LLM APPROVE when a check returned FAILED.
- **No approve on critical/high issues** — even if the LLM says APPROVE, blocking-severity issues force REQUEST\_CHANGES.
- **Invalid LLM output** — a plain comment is posted and no formal review is submitted.
- **Huge diffs** — diffs over `MAX_DIFF_CHARS` are skipped with a comment rather than silently truncated.

---

## Local development

```bash
cd tools/agent_reviewer
pip install -r requirements.txt

# Run with environment variables pointing at a real PR
export GITHUB_TOKEN=ghp_...
export GITHUB_REPOSITORY=owner/repo
export PR_NUMBER=42
export PR_HEAD_SHA=abc1234
export OLLAMA_MODEL=codellama:13b

python main.py
```
