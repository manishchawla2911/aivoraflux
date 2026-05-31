"""Git operations: branch creation, commit, push, PR.

Wraps GitPython for local ops and PyGithub for PR creation.
Designed so tests can pass an in-memory or temp repo path.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitManager:
    """Local git + GitHub API wrapper.

    Heavyweight imports (git, github) are lazy so tests that don't need them
    can run without the libraries installed.
    """

    def __init__(
        self,
        repo_path: str | Path,
        github_token: Optional[str] = None,
        github_repo: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path)
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        self.github_repo = github_repo or os.getenv("GITHUB_DEFAULT_REPO", "")
        self._repo = None
        self._github = None

    # --------------- local git ---------------

    def _ensure_repo(self):
        if self._repo is None:
            from git import Repo  # type: ignore
            if not (self.repo_path / ".git").exists():
                self._repo = Repo.init(str(self.repo_path))
            else:
                self._repo = Repo(str(self.repo_path))
        return self._repo

    def create_branch(self, branch_name: str) -> str:
        repo = self._ensure_repo()
        if branch_name in [h.name for h in repo.heads]:
            repo.git.checkout(branch_name)
        else:
            repo.git.checkout("-b", branch_name)
        logger.info("git.branch_created", extra={"branch": branch_name})
        return branch_name

    def commit_files(self, files: list[str], message: str) -> str:
        repo = self._ensure_repo()
        if files:
            repo.index.add(files)
        commit = repo.index.commit(message)
        sha = commit.hexsha
        logger.info("git.committed", extra={"sha": sha, "files": files, "message": message})
        return sha

    def push_branch(self, branch_name: str, remote: str = "origin") -> bool:
        repo = self._ensure_repo()
        if remote not in [r.name for r in repo.remotes]:
            logger.warning("git.no_remote", extra={"remote": remote})
            return False
        repo.git.push("-u", remote, branch_name)
        return True

    # --------------- GitHub ---------------

    def _ensure_github(self):
        if self._github is None:
            from github import Github  # type: ignore
            self._github = Github(self.github_token)
        return self._github

    def create_pr(self, title: str, body: str, head: str, base: str = "main") -> Optional[str]:
        if not self.github_token or not self.github_repo:
            logger.warning("git.github_unconfigured")
            return None
        gh = self._ensure_github()
        repo = gh.get_repo(self.github_repo)
        pr = repo.create_pull(title=title, body=body, head=head, base=base)
        return pr.html_url
