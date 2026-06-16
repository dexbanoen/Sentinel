"""
main.py - Entry point for the Agent PR Reviewer.

Reads configuration from environment variables, orchestrates the four-step
review pipeline, and exits non-zero only on unexpected errors.
"""

import logging
import os
import sys

from checks import run_checks
from github_client import GitHubClient
from llm import LLMClient
from reviewer import decide_and_submit

# Force UTF-8 output so Unicode in log messages (emojis in review bodies, etc.)
# does not crash on Windows consoles that default to cp1252.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("agent")


def _require_env(name: str) -> str:
    """Read a required environment variable, or exit with a clear error."""
    value = os.environ.get(name, "").strip()
    if not value:
        log.error("Required environment variable %s is not set.", name)
        sys.exit(1)
    return value


def main() -> None:
    # --- Configuration ---
    github_token = _require_env("GITHUB_TOKEN")
    repo_name    = _require_env("GITHUB_REPOSITORY")   # "owner/repo"
    pr_number    = int(_require_env("PR_NUMBER"))
    head_sha     = _require_env("PR_HEAD_SHA")

    ollama_host    = os.environ.get("OLLAMA_HOST",    "http://localhost:11434")
    ollama_model   = os.environ.get("OLLAMA_MODEL",   "llama3.1")
    max_diff_chars = int(os.environ.get("MAX_DIFF_CHARS", "20000"))

    log.info("=== Agent PR Reviewer starting ===")
    log.info("Repository     : %s", repo_name)
    log.info("PR number      : %d", pr_number)
    log.info("Head SHA       : %s", head_sha)
    log.info("Ollama host    : %s", ollama_host)
    log.info("Ollama model   : %s", ollama_model)
    log.info("Max diff chars : %d", max_diff_chars)

    # --- Initialise clients (no network calls yet) ---
    gh  = GitHubClient(token=github_token, repo_name=repo_name)
    llm = LLMClient(host=ollama_host, model=ollama_model)

    # --- Step 1: Fetch the PR diff ---
    log.info("--- Step 1: Fetching PR diff ---")
    diff = gh.get_pr_diff(pr_number)

    if not diff:
        log.warning("PR diff is empty - nothing to review.")
        gh.post_comment(
            pr_number,
            "**Agent**: The diff for this PR is empty. No review was performed.",
        )
        return

    log.info("Diff size: %d characters.", len(diff))

    if len(diff) > max_diff_chars:
        log.warning(
            "Diff is %d chars, exceeds the %d-char limit. Skipping LLM review.",
            len(diff),
            max_diff_chars,
        )
        gh.post_comment(
            pr_number,
            f"**Agent**: This PR's diff is too large ({len(diff):,} characters, "
            f"limit is {max_diff_chars:,}). Automated LLM review was skipped. "
            "Please request a manual review.",
        )
        return

    # --- Step 2: Run local quality checks ---
    log.info("--- Step 2: Running basic checks ---")
    check_results = run_checks()

    # --- Step 3: Ask the LLM to review the diff ---
    log.info("--- Step 3: Requesting LLM review ---")
    llm_result = llm.review(diff=diff, check_results=check_results)

    # --- Step 4: Apply safety rules and post the review ---
    log.info("--- Step 4: Submitting GitHub review ---")
    decide_and_submit(
        gh=gh,
        pr_number=pr_number,
        head_sha=head_sha,
        check_results=check_results,
        llm_result=llm_result,
    )

    log.info("=== Agent PR Reviewer finished ===")


if __name__ == "__main__":
    main()
