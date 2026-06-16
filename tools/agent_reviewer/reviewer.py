"""
reviewer.py — Decision logic: turns check results + LLM output into a
GitHub PR review action.

Rules (hard-coded invariants — cannot be overridden by the LLM):
  1. NEVER call any merge endpoint.
  2. NEVER approve if any check returned FAILED.
  3. NEVER approve if the LLM reported a critical or high-severity issue.
  4. If the LLM JSON could not be parsed, post a COMMENT saying the review
     could not be completed safely, then abort.
  5. Otherwise, honour the LLM's overall_verdict.
"""

import logging
from typing import List

from checks import CheckResult, CheckStatus
from github_client import GitHubClient
from llm import LLMReviewResult

log = logging.getLogger("agent.reviewer")

# Severity levels that block an APPROVE outcome
_BLOCKING_SEVERITIES = {"critical", "high"}


def _checks_passed(check_results: List[CheckResult]) -> bool:
    return all(c.status != CheckStatus.FAILED for c in check_results)


def _has_blocking_issues(llm_result: LLMReviewResult) -> bool:
    for issue in llm_result.issues:
        severity = str(issue.get("severity", "")).lower()
        if severity in _BLOCKING_SEVERITIES:
            return True
    return False


def _format_issues_table(issues: list) -> str:
    if not issues:
        return "_No issues identified._"
    lines = ["| Severity | File | Line | Description |", "| --- | --- | --- | --- |"]
    for issue in issues:
        sev = issue.get("severity", "?")
        file_ = issue.get("file", "general")
        line = issue.get("line") or "–"
        desc = issue.get("description", "")
        lines.append(f"| {sev} | `{file_}` | {line} | {desc} |")
    return "\n".join(lines)


def _format_checks_summary(check_results: List[CheckResult]) -> str:
    icons = {CheckStatus.PASSED: "✅", CheckStatus.FAILED: "❌", CheckStatus.SKIPPED: "⏭️"}
    lines = []
    for c in check_results:
        icon = icons.get(c.status, "?")
        lines.append(f"- {icon} **{c.name}**: {c.status.value.upper()}")
    return "\n".join(lines) if lines else "_No checks run._"


def _build_review_body(
    llm_result: LLMReviewResult,
    check_results: List[CheckResult],
    final_event: str,
) -> str:
    """Compose the Markdown body shown in the GitHub PR review."""
    emoji_map = {
        "APPROVE": "✅",
        "COMMENT": "💬",
        "REQUEST_CHANGES": "🔴",
    }
    emoji = emoji_map.get(final_event, "🤖")

    issues_table = _format_issues_table(llm_result.issues)
    checks_section = _format_checks_summary(check_results)

    body = f"""\
## {emoji} Agent Code Review

**Summary:** {llm_result.summary or "_Not provided._"}

---

### Automated Checks

{checks_section}

---

### Issues Found

{issues_table}

---

### Reasoning

{llm_result.reasoning or "_Not provided._"}

---

<sub>🤖 This review was generated automatically by the Agent LLM reviewer. \
It is advisory and should not replace human judgement.</sub>
""".strip()
    return body


def decide_and_submit(
    gh: GitHubClient,
    pr_number: int,
    head_sha: str,
    check_results: List[CheckResult],
    llm_result: LLMReviewResult,
) -> None:
    """
    Apply safety rules, choose the review event, and submit it to GitHub.
    """
    # ------------------------------------------------------------------
    # Guard: LLM parse failure → safe comment, no formal review
    # ------------------------------------------------------------------
    if llm_result.parse_error:
        log.warning("LLM parse error detected: %s", llm_result.parse_error)
        gh.post_comment(
            pr_number,
            f"⚠️ **Agent**: The LLM review could not be completed safely.\n\n"
            f"**Reason:** `{llm_result.parse_error}`\n\n"
            "Please request a manual review. The automated review has been aborted.",
        )
        return

    # ------------------------------------------------------------------
    # Determine the final review event
    # ------------------------------------------------------------------
    checks_ok = _checks_passed(check_results)
    blocking = _has_blocking_issues(llm_result)

    log.info(
        "Decision factors — checks_ok=%s, llm_blocking=%s, llm_verdict=%r",
        checks_ok,
        blocking,
        llm_result.overall_verdict,
    )

    # Start from the LLM's recommendation, then apply hard overrides.
    final_event: str

    if llm_result.overall_verdict == "approve" and checks_ok and not blocking:
        final_event = "APPROVE"
    elif llm_result.overall_verdict == "request_changes" or blocking:
        final_event = "REQUEST_CHANGES"
    else:
        final_event = "COMMENT"

    # Hard override: if any check failed, we cannot approve.
    if not checks_ok and final_event == "APPROVE":
        log.warning("Downgrading APPROVE → REQUEST_CHANGES because checks failed.")
        final_event = "REQUEST_CHANGES"

    log.info("Final review event: %s", final_event)

    # ------------------------------------------------------------------
    # Build review body and submit
    # ------------------------------------------------------------------
    body = _build_review_body(
        llm_result=llm_result,
        check_results=check_results,
        final_event=final_event,
    )

    gh.submit_review(
        pr_number=pr_number,
        head_sha=head_sha,
        event=final_event,
        body=body,
    )
