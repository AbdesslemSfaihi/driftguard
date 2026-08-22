"""Thin, explicit wrapper over the GitHub REST API (version 2022-11-28).

Why not PyGithub: this project's defense requires understanding every request —
auth header, pagination, rate limits, 404 semantics. A ~100-line wrapper we own
beats a large dependency we'd have to explain second-hand. The `transport`
parameter exists for tests: httpx.MockTransport lets the whole pipeline run
against a fake GitHub with zero network.
"""

import base64
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("driftguard.github")

API_BASE_URL = "https://api.github.com"
PAGE_SIZE = 100
MAX_CHANGED_FILES = 300  # budget guard; truncation is LOGGED, never silent


class GitHubError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"GitHub API error {status_code}: {message}")
        self.status_code = status_code


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str  # added | modified | removed | renamed | changed
    previous_path: str | None  # set when status == "renamed"


class GitHubClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=API_BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "driftguard/0.1",
            },
            timeout=10.0,
            transport=transport,
        )

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def pr_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        """All files touched by a PR. Paginated: GitHub returns at most 100 per page."""
        files: list[ChangedFile] = []
        page = 1
        while True:
            response = self._http.get(
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": PAGE_SIZE, "page": page},
            )
            if response.status_code >= 400:
                raise GitHubError(response.status_code, response.text[:200])
            batch = response.json()
            files.extend(
                ChangedFile(f["filename"], f["status"], f.get("previous_filename"))
                for f in batch
            )
            if len(batch) < PAGE_SIZE or len(files) >= MAX_CHANGED_FILES:
                break
            page += 1
        if len(files) >= MAX_CHANGED_FILES:
            log.warning("PR %s#%s: truncated to %s changed files", repo, number, MAX_CHANGED_FILES)
        return files[:MAX_CHANGED_FILES]

    def file_content(self, repo: str, path: str, ref: str) -> str | None:
        """Decoded text of one file at one commit, or None when unusable.

        None (rather than an exception) covers the *expected* misses: the file
        does not exist at that ref (404), it is a directory, it is binary, or it
        exceeds the 1MB contents-API limit (encoding != base64). Callers treat
        None as 'no source to compare'. Real API failures still raise.
        """
        response = self._http.get(f"/repos/{repo}/contents/{path}", params={"ref": ref})
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        data = response.json()
        if isinstance(data, list) or data.get("encoding") != "base64":
            return None
        try:
            return base64.b64decode(data["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            log.warning("could not decode %s@%s as utf-8 text", path, ref)
            return None
