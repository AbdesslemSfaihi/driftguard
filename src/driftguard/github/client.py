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


@dataclass(frozen=True)
class PullRequestRef:
    number: int
    head: str  # head branch name
    html_url: str


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
        result = self.file_with_sha(repo, path, ref)
        return None if result is None else result[0]

    def file_with_sha(self, repo: str, path: str, ref: str) -> tuple[str, str] | None:
        """(decoded text, blob sha) of one file at one commit, or None when unusable.

        The blob sha is what the contents API demands back when UPDATING the
        file — GitHub's optimistic-concurrency check against lost updates.

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
            return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]
        except (ValueError, UnicodeDecodeError):
            log.warning("could not decode %s@%s as utf-8 text", path, ref)
            return None

    def markdown_paths(self, repo: str, ref: str) -> list[str]:
        """Every .md file path in the repo at one commit, via the git trees API.

        One request for the whole tree (recursive) instead of walking the
        contents API directory by directory. GitHub truncates giant trees;
        that is LOGGED, never silent — same policy as MAX_CHANGED_FILES.
        """
        response = self._http.get(f"/repos/{repo}/git/trees/{ref}", params={"recursive": "1"})
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        data = response.json()
        if data.get("truncated"):
            log.warning("%s@%s: git tree truncated — some docs may be missed", repo, ref)
        return sorted(
            entry["path"]
            for entry in data.get("tree", [])
            if entry.get("type") == "blob" and entry["path"].endswith(".md")
        )

    def ref_sha(self, repo: str, branch: str) -> str:
        """Current head commit sha of a branch — the base for follow-up branches
        created long after the original merge commit went stale."""
        response = self._http.get(f"/repos/{repo}/git/ref/heads/{branch}")
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return response.json()["object"]["sha"]

    # ---------- write operations (docs PR publishing) ----------

    def create_issue_comment(self, repo: str, number: int, body: str) -> int:
        """Post a comment on a PR (PRs are issues to this endpoint). Returns the
        comment id, which the questions table records for the audit trail."""
        response = self._http.post(
            f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return response.json()["id"]

    def create_branch(self, repo: str, branch: str, from_sha: str) -> None:
        response = self._http.post(
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": from_sha},
        )
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])

    def put_file(
        self, repo: str, path: str, *, message: str, text: str, branch: str, sha: str | None
    ) -> None:
        """Create or update one file on a branch. *sha* is the current blob sha
        when updating (from file_with_sha); GitHub rejects a stale sha, which
        protects against clobbering a concurrent edit."""
        payload: dict[str, str] = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha is not None:
            payload["sha"] = sha
        response = self._http.put(f"/repos/{repo}/contents/{path}", json=payload)
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])

    def open_pull_request(self, repo: str, *, title: str, body: str, head: str, base: str) -> str:
        """Open a PR and return its html_url."""
        response = self._http.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return response.json()["html_url"]

    # ---------- demo and maintenance operations (cli/demo.py) ----------

    def merge_pull_request(self, repo: str, number: int) -> str:
        """Merge an open PR with a merge commit, like the GitHub button. Returns
        the merge commit sha. GitHub then sends the same `pull_request closed`
        webhook a human merge would."""
        response = self._http.put(
            f"/repos/{repo}/pulls/{number}/merge", json={"merge_method": "merge"}
        )
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return response.json()["sha"]

    def open_pull_requests(self, repo: str) -> list[PullRequestRef]:
        response = self._http.get(f"/repos/{repo}/pulls", params={"state": "open", "per_page": 100})
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return [
            PullRequestRef(pr["number"], pr["head"]["ref"], pr["html_url"])
            for pr in response.json()
        ]

    def close_pull_request(self, repo: str, number: int) -> None:
        response = self._http.patch(f"/repos/{repo}/pulls/{number}", json={"state": "closed"})
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])

    def branches_with_prefix(self, repo: str, prefix: str) -> list[str]:
        """Branch names starting with *prefix* ("demo/"), via matching-refs."""
        response = self._http.get(f"/repos/{repo}/git/matching-refs/heads/{prefix}")
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return [entry["ref"].removeprefix("refs/heads/") for entry in response.json()]

    def delete_branch(self, repo: str, branch: str) -> None:
        response = self._http.delete(f"/repos/{repo}/git/refs/heads/{branch}")
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])

    def default_branch(self, repo: str) -> str:
        response = self._http.get(f"/repos/{repo}")
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        return response.json()["default_branch"]
