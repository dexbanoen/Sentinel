"""
llm.py — Client for communicating with a local Ollama instance.

Uses Ollama's /api/chat endpoint with the chat interface so we can pass
a system prompt and a user message separately, which typically yields
better-structured JSON output from instruction-tuned models.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from checks import CheckResult, CheckStatus
from prompts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger("agent.llm")

# Hard limit: if the LLM returns a response body larger than this many
# characters we treat it as invalid to avoid runaway memory use.
_MAX_RESPONSE_CHARS = 50_000


@dataclass
class LLMReviewResult:
    """
    Parsed result from the LLM.

    If `parse_error` is set the downstream code should leave a safe comment
    rather than submitting a formal review.
    """
    raw_json: str = ""
    summary: str = ""
    issues: List[Dict[str, Any]] = field(default_factory=list)
    overall_verdict: str = "comment"  # "approve" | "comment" | "request_changes"
    reasoning: str = ""
    parse_error: Optional[str] = None


class LLMClient:
    def __init__(self, host: str, model: str) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._chat_url = f"{self._host}/api/chat"
        log.info("LLM client initialised: model=%s endpoint=%s", model, self._chat_url)

    def _build_check_summary(self, check_results: List[CheckResult]) -> str:
        lines = []
        for c in check_results:
            icon = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}.get(c.status.value, "?")
            lines.append(f"{icon} **{c.name}**: {c.status.value.upper()}")
            if c.output and c.status != CheckStatus.SKIPPED:
                # Include a snippet of the output (first 500 chars) for context
                snippet = c.output[:500]
                lines.append(f"```\n{snippet}\n```")
        return "\n".join(lines) if lines else "No checks were run."

    def review(
        self,
        diff: str,
        check_results: List[CheckResult],
    ) -> LLMReviewResult:
        """
        Send the diff and check results to the LLM and parse the JSON response.

        Returns an LLMReviewResult; if the response cannot be parsed,
        `parse_error` will be set and the caller should post a safe comment.
        """
        check_summary = self._build_check_summary(check_results)
        user_content = build_user_prompt(diff=diff, check_summary=check_summary)

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "format": "json",  # Ollama JSON mode — forces JSON output on supported models
            "options": {
                "temperature": 0.1,  # low temperature for deterministic structured output
                "num_predict": 2048,
            },
        }

        log.info(
            "Sending request to Ollama (model=%s, diff=%d chars).",
            self._model,
            len(diff),
        )

        try:
            response = requests.post(
                self._chat_url,
                json=payload,
                timeout=300,  # local models can be slow; 5-minute ceiling
            )
            response.raise_for_status()
        except requests.Timeout:
            log.error("Ollama request timed out after 300 seconds.")
            return LLMReviewResult(parse_error="LLM request timed out.")
        except requests.RequestException as exc:
            log.error("Ollama request failed: %s", exc)
            return LLMReviewResult(parse_error=f"LLM request error: {exc}")

        # ------------------------------------------------------------------
        # Extract the message content from Ollama's response envelope
        # ------------------------------------------------------------------
        try:
            envelope = response.json()
            raw_content: str = envelope["message"]["content"]
        except (KeyError, ValueError) as exc:
            log.error("Unexpected Ollama response structure: %s", exc)
            return LLMReviewResult(
                raw_json=response.text[:500],
                parse_error=f"Unexpected Ollama response structure: {exc}",
            )

        if len(raw_content) > _MAX_RESPONSE_CHARS:
            log.error(
                "LLM response too large (%d chars). Treating as invalid.",
                len(raw_content),
            )
            return LLMReviewResult(
                parse_error=f"LLM response too large ({len(raw_content)} chars)."
            )

        log.info("Received LLM response (%d chars). Parsing JSON...", len(raw_content))
        log.debug("Raw LLM output:\n%s", raw_content)

        # ------------------------------------------------------------------
        # Parse and validate the JSON payload
        # ------------------------------------------------------------------
        return self._parse_response(raw_content)

    def _parse_response(self, raw_content: str) -> LLMReviewResult:
        """
        Parse and lightly validate the LLM JSON output.

        We are intentionally lenient: if the model wraps the JSON in a
        markdown code fence we strip it first.
        """
        # Strip optional markdown fence (```json ... ```)
        content = raw_content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            # Remove first and last fence lines
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            content = "\n".join(inner).strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            log.error("JSON parse error: %s", exc)
            return LLMReviewResult(
                raw_json=raw_content[:500],
                parse_error=f"JSON parse error: {exc}",
            )

        if not isinstance(data, dict):
            return LLMReviewResult(
                raw_json=raw_content[:500],
                parse_error="LLM returned JSON but it is not an object.",
            )

        # Validate required top-level keys
        required = {"summary", "issues", "overall_verdict", "reasoning"}
        missing = required - data.keys()
        if missing:
            log.warning("LLM JSON is missing keys: %s. Will use defaults.", missing)

        verdict = str(data.get("overall_verdict", "comment")).lower()
        if verdict not in {"approve", "comment", "request_changes"}:
            log.warning("Unexpected verdict %r — defaulting to 'comment'.", verdict)
            verdict = "comment"

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            log.warning("'issues' field is not a list — resetting to empty.")
            issues = []

        return LLMReviewResult(
            raw_json=raw_content,
            summary=str(data.get("summary", "")).strip(),
            issues=issues,
            overall_verdict=verdict,
            reasoning=str(data.get("reasoning", "")).strip(),
        )
