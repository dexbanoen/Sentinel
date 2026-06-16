"""
github_client.py — Thin wrapper around the GitHub REST API via PyGithub.

Responsibilities:
  - Fetch the raw unified diff for a pull request.
  - Post plain comments to the PR conversation.
  - Submit a formal PR review (APPROVE / COMMENT / REQUEST_CHANGES).

Intentionally does NOT expose any merge-related functionality.
"""

import logging
from typing import Optional

import requests
from github import Auth, Github, GithubException

log = logging.getLogger("agent.github")


class GitHubClient:
    def __init__(self, token: str, repo_name: str) -> None:
        """
        :param token: A GitHub personal access token or GITHUB_TOKEN secret.
        :param repo_name: Full repository name, e.g. "owner/repo".
        """
        self._token = token
        self._repo_name = repo_name
        auth = Auth.Token(token)
        self._gh = Github(auth=auth)
        self._repo = self._gh.get_repo(repo_name)
        log.info("Connected to GitHub repository: %s", repo_name)

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def get_pr_diff(self, pr_number: int) -> str:
        """
        Return the unified diff of a pull request as a plain string.

        Uses the raw GitHub API with 'Accept: application/vnd.github.v3.diff'
        because PyGithub does not expose the raw diff directly.
        """
        url = f"https://api.github.com/repos/{self._repo_name}/pulls/{pr_number}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        log.info("Fetching diff from %s", url)
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        diff = response.text
        log.info("Fetched diff: %d characters.", len(diff))
        return diff

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def post_comment(self, pr_number: int, body: str) -> None:
        """Post a plain issue comment to the PR conversation."""
        pr = self._repo.get_pull(pr_number)
        pr.create_issue_comment(body)
        log.info("Posted PR comment (%d characters).", len(body))

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    def submit_review(
        self,
        pr_number: int,
        head_sha: str,
        event: str,
        body: str,
    ) -> None:
        """
        Submit a formal GitHub PR review.

        :param event: One of "APPROVE", "REQUEST_CHANGES", "COMMENT".
                      "APPROVE" must never be called when checks failed or
                      the LLM found high-severity issues — enforced by
                      reviewer.py, not here.
        :param body: The review body text shown to the PR author.
        """
        if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise ValueError(f"Invalid review event: {event!r}")

        # Safety guard: we refuse to call any merge endpoint regardless of
        # how this function is invoked.
        log.info(
            "Submitting review event=%s for PR #%d (SHA: %s).",
            event,
            pr_number,
            head_sha,
        )
        try:
            pr = self._repo.get_pull(pr_number)
            pr.create_review(commit_id=head_sha, body=body, event=event)
            log.info("Review submitted successfully.")
        except GithubException as exc:
            log.error("Failed to submit review: %s", exc)
            raise
