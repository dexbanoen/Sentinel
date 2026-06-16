"""
checks.py — Run basic quality checks on the repository if the tools exist.

Checks attempted (in order):
  1. Tests   — pytest, or npm test, depending on what's found.
  2. Lint    — ruff, flake8, or eslint.
  3. Typecheck — mypy or tsc.

Each check is attempted; if the tool is not installed it is recorded as
SKIPPED rather than FAILED so that repos without that tooling are not
penalised.

Returns a list of CheckResult dataclass instances that downstream
components can inspect.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import List

log = logging.getLogger("agent.checks")


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    output: str = ""
    return_code: int = 0


def _run(cmd: List[str], name: str, timeout: int = 120) -> CheckResult:
    """Run a shell command and return a CheckResult."""
    tool = cmd[0]
    tool_path = shutil.which(tool)
    if not tool_path:
        log.info("Check [%s]: tool '%s' not found — SKIPPED.", name, tool)
        return CheckResult(name=name, status=CheckStatus.SKIPPED, output=f"Tool '{tool}' not found.")

    # Use the full resolved path as the executable.
    # On Windows, tools like npm are npm.cmd (batch files). subprocess.run
    # needs the full path with extension — the bare name "npm" causes WinError 2.
    full_cmd = [tool_path] + cmd[1:]
    log.info("Check [%s]: running %s ...", name, " ".join(full_cmd))
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (result.stdout + "\n" + result.stderr).strip()
        status = CheckStatus.PASSED if result.returncode == 0 else CheckStatus.FAILED
        log.info(
            "Check [%s]: %s (exit code %d).",
            name,
            status.value.upper(),
            result.returncode,
        )
        if combined:
            # Truncate very long outputs so they don't swamp the LLM prompt.
            if len(combined) > 3000:
                combined = combined[:3000] + "\n... [output truncated]"
            log.debug("Check [%s] output:\n%s", name, combined)
        return CheckResult(
            name=name,
            status=status,
            output=combined,
            return_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        log.warning("Check [%s]: timed out after %ds.", name, timeout)
        return CheckResult(
            name=name,
            status=CheckStatus.FAILED,
            output=f"Check timed out after {timeout} seconds.",
        )


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_tests() -> CheckResult:
    """Try pytest first, then npm test."""
    if shutil.which("pytest"):
        return _run(["pytest", "--tb=short", "-q"], name="tests")
    if shutil.which("npm"):
        return _run(["npm", "test", "--if-present"], name="tests")
    return CheckResult(
        name="tests",
        status=CheckStatus.SKIPPED,
        output="No supported test runner found (pytest, npm).",
    )


def _check_lint() -> CheckResult:
    """Try ruff, then flake8, then eslint."""
    if shutil.which("ruff"):
        return _run(["ruff", "check", "."], name="lint")
    if shutil.which("flake8"):
        return _run(["flake8", "."], name="lint")
    if shutil.which("eslint"):
        return _run(["eslint", "."], name="lint")
    return CheckResult(
        name="lint",
        status=CheckStatus.SKIPPED,
        output="No supported linter found (ruff, flake8, eslint).",
    )


def _check_typecheck() -> CheckResult:
    """Try mypy, then tsc."""
    if shutil.which("mypy"):
        return _run(["mypy", ".", "--ignore-missing-imports"], name="typecheck")
    if shutil.which("tsc"):
        return _run(["tsc", "--noEmit"], name="typecheck")
    return CheckResult(
        name="typecheck",
        status=CheckStatus.SKIPPED,
        output="No supported type checker found (mypy, tsc).",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_checks() -> List[CheckResult]:
    """
    Run all checks and return the results.

    Checks that are skipped are still included in the list so the LLM
    knows which tools were not available.
    """
    checks = [
        _check_tests(),
        _check_lint(),
        _check_typecheck(),
    ]
    for c in checks:
        log.info("  %-12s → %s", c.name, c.status.value.upper())
    return checks
