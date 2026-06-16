"""
reviewer.py - Decision logic: turns check results + LLM output into a GitHub PR review.

Hard safety rules (cannot be overridden by the LLM):
  1. Never call any merge endpoint.
  2. Never APPROVE if any check returned FAILED.
  3. Never APPROVE if the LLM reported a critical or high-severity issue.
  4. If the LLM JSON could not be parsed, post a safe COMMENT and abort.
  5. Otherwise, honour the LLM's overall_verdict.
"""

import logging
from typing import List

from checks import CheckResult, CheckStatus
from github_client import GitHubClient
from llm import LLMReviewResult

log = logging.getLogger("agent.reviewer")

# Issues at these severity levels block an APPROVE verdict.
_BLOCKING_SEVERITIES = {"critical", "high"}


def _checks_passed(check_results: List[CheckResult]) -> bool:
    """Return True if no check returned FAILED (SKIPPED is acceptable)."""
    return all(c.status != CheckStatus.FAILED for c in check_results)


def _has_blocking_issues(llm_result: LLMReviewResult) -> bool:
    """Return True if any LLM issue is at critical or high severity."""
    return any(
        str(issue.get("severity", "")).lower() in _BLOCKING_SEVERITIES
        for issue in llm_result.issues
    )


def _format_issues_table(issues: list) -> str:
    """Render the LLM issues list as a Markdown table."""
    if not issues:
        return "_No issues identified._"

    rows = ["| Severity | File | Line | Description |", "| --- | --- | --- | --- |"]
    for issue in issues:
        sev   = issue.get("severity", "?")
        file_ = issue.get("file", "general")
        line  = issue.get("line") or "-"
        desc  = issue.get("description", "")
        rows.append(f"| {sev} | `{file_}` | {line} | {desc} |")
    return "\n".join(rows)


def _format_checks_summary(check_results: List[CheckResult]) -> str:
    """Render check results as a Markdown list with status icons."""
    icons = {
        CheckStatus.PASSED:  "✅",
        CheckStatus.FAILED:  "❌",
        CheckStatus.SKIPPED: "⏭️",
    }
    lines = [
        f"- {icons.get(c.status, '?')} **{c.name}**: {c.status.value.upper()}"
        for c in check_results
    ]
    return "\n".join(lines) if lines else "_No checks run._"


def _build_review_body(
    llm_result: LLMReviewResult,
    check_results: List[CheckResult],
    final_event: str,
) -> str:
    """Compose the Markdown body shown in the GitHub PR review."""
    emoji = {"APPROVE": "✅", "COMMENT": "💬", "REQUEST_CHANGES": "🔴"}.get(final_event, "🤖")

    return f"""\
## {emoji} Agent Code Review

**Summary:** {llm_result.summary or "_Not provided._"}

---

### Automated Checks

{_format_checks_summary(check_results)}

---

### Issues Found

{_format_issues_table(llm_result.issues)}

---

### Reasoning

{llm_result.reasoning or "_Not provided._"}

---

<sub>🤖 This review was generated automatically by the Agent LLM reviewer. \
It is advisory and should not replace human judgement.</sub>""".strip()


def decide_and_submit(
    gh: GitHubClient,
    pr_number: int,
    head_sha: str,
    check_results: List[CheckResult],
    llm_result: LLMReviewResult,
) -> None:
    """Apply safety rules, choose the review event, and post it to GitHub."""

    # If the LLM returned unparseable output, post a safe comment and stop.
    # A failed parse must never silently become an approval.
    if llm_result.parse_error:
        log.warning("LLM parse error: %s", llm_result.parse_error)
        gh.post_comment(
            pr_number,
            f"**Agent**: The LLM review could not be completed safely.\n\n"
            f"**Reason:** `{llm_result.parse_error}`\n\n"
            "Please request a manual review.",
        )
        return

    checks_ok = _checks_passed(check_results)
    blocking  = _has_blocking_issues(llm_result)

    log.info(
        "Decision: checks_ok=%s, llm_blocking=%s, llm_verdict=%r",
        checks_ok,
        blocking,
        llm_result.overall_verdict,
    )

    # Derive the initial event from the LLM verdict, then apply hard overrides.
    if llm_result.overall_verdict == "approve" and checks_ok and not blocking:
        final_event = "APPROVE"
    elif llm_result.overall_verdict == "request_changes" or blocking:
        final_event = "REQUEST_CHANGES"
    else:
        final_event = "COMMENT"

    # Hard override: a failed check always blocks approval, regardless of verdict.
    if not checks_ok and final_event == "APPROVE":
        log.warning("Overriding APPROVE to REQUEST_CHANGES because a check failed.")
        final_event = "REQUEST_CHANGES"

    log.info("Final review event: %s", final_event)

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
