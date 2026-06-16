"""
github_client.py - Thin wrapper around the GitHub REST API via PyGithub.

Exposes exactly three operations:
  - get_pr_diff()    : fetch the unified diff for a pull request
  - post_comment()   : post a plain text comment to the PR conversation
  - submit_review()  : post a formal review (APPROVE / COMMENT / REQUEST_CHANGES)

Merge functionality is intentionally absent from this class.
"""

import logging

import requests
from github import Auth, Github, GithubException

log = logging.getLogger("agent.github")


class GitHubClient:
    def __init__(self, token: str, repo_name: str) -> None:
        """
        Connect to the GitHub API.

        :param token: GITHUB_TOKEN secret (injected automatically by Actions).
        :param repo_name: Full repository name, e.g. "owner/repo".
        """
        self._token = token
        self._repo_name = repo_name
        auth = Auth.Token(token)
        self._gh   = Github(auth=auth)
        self._repo = self._gh.get_repo(repo_name)
        log.info("Connected to GitHub repository: %s", repo_name)

    def get_pr_diff(self, pr_number: int) -> str:
        """
        Return the unified diff of a pull request as plain text.

        PyGithub does not expose the raw diff, so we call the REST API
        directly with the 'application/vnd.github.v3.diff' Accept header.
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
        log.info("Fetched diff: %d characters.", len(response.text))
        return response.text

    def post_comment(self, pr_number: int, body: str) -> None:
        """Post a plain issue comment to the PR conversation."""
        pr = self._repo.get_pull(pr_number)
        pr.create_issue_comment(body)
        log.info("Posted PR comment (%d characters).", len(body))

    def submit_review(
        self,
        pr_number: int,
        head_sha: str,
        event: str,
        body: str,
    ) -> None:
        """
        Submit a formal GitHub PR review.

        :param event: One of "APPROVE", "REQUEST_CHANGES", or "COMMENT".
                      Safety enforcement (no APPROVE on failed checks, etc.)
                      is handled by reviewer.py before this is called.
        :param head_sha: The commit SHA the review is pinned to. GitHub marks
                         the review stale if the author pushes further commits.
        :param body: Markdown body displayed to the PR author.
        """
        if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise ValueError(f"Invalid review event: {event!r}")

        log.info("Submitting review event=%s for PR #%d (SHA: %s).", event, pr_number, head_sha)
        try:
            pr     = self._repo.get_pull(pr_number)
            # PyGithub 2.x requires a Commit object rather than a raw SHA string.
            commit = self._repo.get_commit(head_sha)
            pr.create_review(commit=commit, body=body, event=event)
            log.info("Review submitted successfully.")
        except GithubException as exc:
            log.error("Failed to submit review: %s", exc)
            raise
