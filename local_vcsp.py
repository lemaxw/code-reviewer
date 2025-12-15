import logging
import os
from pathlib import Path
from vcsp_interface import VCSPInterface, PRFile, PR, Commit
from datetime import datetime
from typing import List
import subprocess

logger = logging.getLogger(__name__)

class LocalVCSP(VCSPInterface):
    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path).expanduser()
        self.repo_path = None
        self.repo_type = None  # "git" or "svn"
        self.svn_cmd = os.getenv("SVN_BIN", "svn")
        self.svn_username = os.getenv("SVN_USERNAME")
        self.svn_password = os.getenv("SVN_PASSWORD")
        self.svn_trust_failures = os.getenv("SVN_TRUST_FAILURES")

    def _ensure_repo_path(self, repo_name: str = None):
        """
        Resolve which local repository to operate on.

        If repo_name points to an existing path, prefer it; otherwise fall back
        to the base path provided at initialization.
        """
        proposed_path = None
        if repo_name:
            candidate = Path(repo_name).expanduser()
            if candidate.exists():
                proposed_path = candidate.resolve()

        if proposed_path is None:
            proposed_path = self.base_path.resolve()

        if proposed_path != self.repo_path:
            self.repo_path = proposed_path
            self.repo_type = self._detect_repo_type()
            logger.debug("LocalVCSP repo set to %s (type=%s)", self.repo_path, self.repo_type)

        if not self.repo_path.exists():
            raise ValueError(f"Provided path {self.repo_path} does not exist")
        if self.repo_type is None:
            raise ValueError(f"Provided path {self.repo_path} is not a Git or SVN repository")

    def _run_git(self, args: List[str]) -> str:
        if self.repo_path is None:
            raise RuntimeError("Repository path is not set")
        logger.debug("Running git command: git %s (cwd=%s)", " ".join(args), self.repo_path)
        result = subprocess.run(["git"] + args, cwd=self.repo_path, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
        return result.stdout

    def _auth_flags(self) -> List[str]:
        flags = ["--non-interactive"]
        if self.svn_username:
            flags.extend(["--username", self.svn_username])
        if self.svn_password:
            flags.extend(["--password", self.svn_password])
        if self.svn_trust_failures:
            flags.extend(["--trust-server-cert-failures", self.svn_trust_failures])
        return flags

    def _run_svn(self, args: List[str]) -> str:
        if self.repo_path is None:
            raise RuntimeError("Repository path is not set")
        auth_desc = "with auth" if (self.svn_username or self.svn_password) else "without auth"
        logger.debug(
            "Running svn command: %s %s (cwd=%s, %s)",
            self.svn_cmd,
            " ".join(args),
            self.repo_path,
            auth_desc,
        )
        result = subprocess.run(
            [self.svn_cmd] + args + self._auth_flags(),
            cwd=self.repo_path,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"SVN command failed: {' '.join(args)}\n{result.stderr.decode(errors='replace')}"
            )
        return result.stdout.decode("utf-8", errors="replace")

    def _detect_repo_type(self) -> str:
        """
        Detect whether the repo_path is a Git or SVN working copy.

        Handles cases where control folders are hidden (e.g., .svn on Windows).
        """
        if (self.repo_path / ".git").exists():
            return "git"
        if (self.repo_path / ".svn").exists():
            return "svn"

        def _try_cmd(cmd: List[str]) -> bool:
            result = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)
            return result.returncode == 0

        if _try_cmd(["git", "rev-parse", "--is-inside-work-tree"]):
            return "git"
        if _try_cmd([self.svn_cmd, "info"] + self._auth_flags()):
            return "svn"
        return None

    def get_files_in_pr(self, repo_name: str = None, pr_number: int = None) -> List[PRFile]:
        self._ensure_repo_path(repo_name)
        diff_output = ""
        if self.repo_type == "git":
            logger.debug("Detected git repository; generating diff against working tree.")
            diff_output = self._run_git(["diff", "--unified=0"])
            return self._parse_git_diff(diff_output)
        elif self.repo_type == "svn":
            logger.debug("Detected svn repository; generating diff against working copy.")
            diff_output = self._run_svn(["diff"])
            return self._parse_svn_diff(diff_output)
        else:
            raise RuntimeError("Unsupported repository type for local VCSP")

    def _parse_git_diff(self, diff_text: str) -> List[PRFile]:
        files = []
        current_file = None
        current_diff = []
        changed_lines = set()
        line_num_new = None

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                if current_file:
                    files.append(PRFile(current_file, '\n'.join(current_diff), changed_lines))
                parts = line.split()
                current_file = parts[2][2:] if len(parts) > 2 else "unknown"
                current_diff = [line]
                changed_lines = set()
                line_num_new = None
            elif current_file:
                current_diff.append(line)
                if line.startswith("@@"):
                    import re
                    match = re.search(r"\+(\d+)", line)
                    if match:
                        line_num_new = int(match.group(1)) - 1
                elif line.startswith("+") and not line.startswith("+++"):
                    if line_num_new is not None:
                        line_num_new += 1
                        changed_lines.add(line_num_new)
                elif not line.startswith("-"):
                    if line_num_new is not None:
                        line_num_new += 1

        if current_file:
            files.append(PRFile(current_file, '\n'.join(current_diff), changed_lines))

        return files

    def _parse_svn_diff(self, diff_text: str) -> List[PRFile]:
        files = []
        current_file = None
        current_diff = []
        changed_lines = set()
        line_num_new = None

        for line in diff_text.splitlines():
            if line.startswith("Index: "):
                if current_file:
                    files.append(PRFile(current_file, "\n".join(current_diff), changed_lines))
                current_file = line[len("Index: "):].strip()
                current_diff = [line]
                changed_lines = set()
                line_num_new = None
                continue

            if current_file:
                current_diff.append(line)
                if line.startswith("@@"):
                    import re
                    match = re.search(r"\+(\d+)", line)
                    if match:
                        line_num_new = int(match.group(1)) - 1
                elif line.startswith("+") and not line.startswith("+++"):
                    if line_num_new is not None:
                        line_num_new += 1
                        changed_lines.add(line_num_new)
                elif not line.startswith("-"):
                    if line_num_new is not None:
                        line_num_new += 1

        if current_file:
            files.append(PRFile(current_file, "\n".join(current_diff), changed_lines))

        return files

    def get_file_content(self, repo_name: str, file_path: str, ref: str = "HEAD"):
        self._ensure_repo_path(repo_name)
        path = self.repo_path / file_path
        if not path.exists():
            logger.warning("File %s does not exist", path)
            return None
        with path.open("rb") as f:
            content = f.read()
        from types import SimpleNamespace
        return SimpleNamespace(decoded_content=content)

    def create_review_comment(self, repo_name: str, commit: str, file_path: str, line: int, comment: str, side: str):
        self._ensure_repo_path(repo_name)
        logger.info("[REVIEW] %s:%d - %s", file_path, line, comment)
        return {"file": file_path, "line": line, "comment": comment}

    def get_pull_request(self, repo_name: str, pr_number: int) -> PR:        
        self._ensure_repo_path(repo_name)
        head = "HEAD"
        return PR(title="Local Review", body="Uncommitted local changes", head_sha=head, state="OPEN")

    def get_commit(self, repo_name: str, commit_sha: str) -> Commit:
        self._ensure_repo_path(repo_name)
        if self.repo_type == "git" and commit_sha == "HEAD":
            message = self._run_git(["log", "-1", "--pretty=%B", commit_sha]).strip()
            author = self._run_git(["log", "-1", "--pretty=%an", commit_sha]).strip()
            date = self._run_git(["log", "-1", "--pretty=%ad", commit_sha]).strip()
            return Commit(sha=commit_sha, message=message, author=author, date=date)
        if self.repo_type == "svn":
            rev = commit_sha if commit_sha and commit_sha != "HEAD" else "HEAD"
            log_output = self._run_svn(["log", "-r", str(rev), "-l", "1"])
            lines = log_output.splitlines()
            message = ""
            author = ""
            date = ""
            # Parse standard svn log single entry format
            for idx, line in enumerate(lines):
                if line.startswith("r") and "|" in line:
                    parts = [part.strip() for part in line.split("|")]
                    if len(parts) >= 3:
                        author = parts[1]
                        date = parts[2]
                if line == "":
                    message = "\n".join(lines[idx + 1:]).strip()
                    break
            return Commit(sha=str(rev), message=message, author=author, date=date)

    def get_repository(self, repo_name: str):
        self._ensure_repo_path(repo_name)
        return self.repo_path.name
