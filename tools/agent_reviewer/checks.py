"""
checks.py - Runs basic quality checks against the repository.

Checks attempted (in order):
  1. Tests      - pytest, or npm test (only if package.json exists)
  2. Lint       - ruff, flake8, or eslint
  3. Typecheck  - mypy or tsc

Each check gracefully skips if the required tool is not installed,
so repos without that tooling are not penalised with a FAILED status.
"""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import List

log = logging.getLogger("agent.checks")


class CheckStatus(str, Enum):
    PASSED  = "passed"
    FAILED  = "failed"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single quality check."""
    name:        str
    status:      CheckStatus
    output:      str = ""
    return_code: int = 0


def _run(cmd: List[str], name: str, timeout: int = 120) -> CheckResult:
    """
    Resolve the tool path and run the command as a subprocess.

    Uses shutil.which() to find the full executable path before calling
    subprocess.run(). On Windows this is required because tools like npm
    are batch files (npm.cmd) — passing the bare name causes WinError 2.
    """
    tool      = cmd[0]
    tool_path = shutil.which(tool)

    if not tool_path:
        log.info("Check [%s]: '%s' not installed - SKIPPED.", name, tool)
        return CheckResult(
            name=name,
            status=CheckStatus.SKIPPED,
            output=f"Tool '{tool}' not found on PATH.",
        )

    full_cmd = [tool_path] + cmd[1:]
    log.info("Check [%s]: running %s", name, " ".join(full_cmd))

    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (result.stdout + "\n" + result.stderr).strip()
        status   = CheckStatus.PASSED if result.returncode == 0 else CheckStatus.FAILED

        log.info("Check [%s]: %s (exit code %d).", name, status.value.upper(), result.returncode)

        # Truncate very long output so it does not flood the LLM prompt.
        if len(combined) > 3000:
            combined = combined[:3000] + "\n... [output truncated]"

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


# Individual checks --------------------------------------------------------

def _check_tests() -> CheckResult:
    """Run pytest if available, otherwise npm test (requires package.json)."""
    if shutil.which("pytest"):
        return _run(["pytest", "--tb=short", "-q"], name="tests")
    if shutil.which("npm") and os.path.exists("package.json"):
        return _run(["npm", "test", "--if-present"], name="tests")
    return CheckResult(
        name="tests",
        status=CheckStatus.SKIPPED,
        output="No supported test runner found (pytest, npm).",
    )


def _check_lint() -> CheckResult:
    """Run ruff, flake8, or eslint - whichever is installed first."""
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
    """Run mypy or tsc - whichever is installed first."""
    if shutil.which("mypy"):
        return _run(["mypy", ".", "--ignore-missing-imports"], name="typecheck")
    if shutil.which("tsc"):
        return _run(["tsc", "--noEmit"], name="typecheck")
    return CheckResult(
        name="typecheck",
        status=CheckStatus.SKIPPED,
        output="No supported type checker found (mypy, tsc).",
    )


# Public API ---------------------------------------------------------------

def run_checks() -> List[CheckResult]:
    """Run all checks and return results. SKIPPED counts are included."""
    checks = [
        _check_tests(),
        _check_lint(),
        _check_typecheck(),
    ]
    for c in checks:
        log.info("  %-12s %s", c.name, c.status.value.upper())
    return checks
