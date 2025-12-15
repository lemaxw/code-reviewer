import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional
import os

from vcsp_interface import Commit, PR, PRFile, VCSPInterface

logger = logging.getLogger(__name__)


class SvnVCSP(VCSPInterface):
    """
    Minimal SVN implementation of the VCSPInterface.

    SVN has no concept of pull requests or inline comments, so we work with a
    single revision number and emit findings to stdout instead of posting
    comments upstream.
    """

    supports_comments = False

    def __init__(self):
        self.svn_username = os.getenv("SVN_USERNAME")
        self.svn_password = os.getenv("SVN_PASSWORD")
        self.trust_failures = os.getenv("SVN_TRUST_FAILURES")
        self.svn_cmd = os.getenv("SVN_BIN", "svn")

    def _auth_flags(self) -> List[str]:
        flags = ["--non-interactive"]
        if self.svn_username:
            flags.extend(["--username", self.svn_username])
        if self.svn_password:
            flags.extend(["--password", self.svn_password])
        if self.trust_failures:
            flags.extend(["--trust-server-cert-failures", self.trust_failures])
        return flags

    def _is_local_repo(self, repo_name: str) -> bool:
        return Path(repo_name).exists()

    def _repo_cwd(self, repo_name: str) -> Optional[str]:
        return repo_name if self._is_local_repo(repo_name) else None

    def _repo_target(self, repo_name: str) -> Optional[str]:
        return None if self._is_local_repo(repo_name) else repo_name

    def _run_svn(self, repo_name: str, args: List[str]) -> str:
        cmd = [self.svn_cmd] + args + self._auth_flags()
        cwd = self._repo_cwd(repo_name)
        target = self._repo_target(repo_name)
        if target:
            cmd.append(target)

        result = subprocess.run(cmd, cwd=cwd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"svn command failed ({' '.join(cmd)}): {result.stderr.decode(errors='replace').strip()}"
            )
        return result.stdout.decode("utf-8", errors="replace")

    def _parse_log_message(self, log_output: str) -> str:
        """
        Extract the commit message body from `svn log` output.
        """
        lines = log_output.splitlines()
        message_lines = []
        capturing = False

        for line in lines:
            if line.startswith("-----"):
                if capturing:
                    break
                continue
            if not capturing:
                if line.strip() == "":
                    capturing = True
                continue
            message_lines.append(line)

        return "\n".join(message_lines).strip()

    def _parse_diff(self, diff_text: str) -> List[PRFile]:
        files = []
        current_file = None
        current_diff = []
        changed_lines = set()
        line_num_new = None

        for line in diff_text.splitlines():
            if line.startswith("Index: "):
                if current_file:
                    files.append(PRFile(current_file, "\n".join(current_diff), changed_lines))
                current_file = line[len("Index: ") :].strip()
                current_diff = [line]
                changed_lines = set()
                line_num_new = None
                continue

            if current_file is not None:
                current_diff.append(line)
                if line.startswith("@@"):
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
        elif diff_text.strip():
            files.append(PRFile("diff.patch", diff_text, set()))

        return files

    def _build_file_target(self, repo_name: str, file_path: str, ref: str) -> str:
        revision = ref or "HEAD"
        if self._is_local_repo(repo_name):
            target_path = Path(repo_name) / file_path
            return f"{target_path}@{revision}"
        repo_root = repo_name.rstrip("/")
        return f"{repo_root}/{file_path}@{revision}"

    def get_pull_request(self, repo_name: str, pr_number: int) -> PR:
        log_output = self._run_svn(repo_name, ["log", "-r", str(pr_number), "-l", "1"])
        message = self._parse_log_message(log_output)
        if not message:
            message = f"SVN revision {pr_number}"
        title, body = (message.split("\n", 1) + [""])[:2]
        return PR(
            title=title,
            body=body.strip(),
            head_sha=str(pr_number),
            state="open",
        )

    def get_files_in_pr(self, repo_name: str, pr_number: int):
        diff_output = self._run_svn(repo_name, ["diff", "-c", str(pr_number)])
        return self._parse_diff(diff_output)

    def get_file_content(self, repo_name: str, file_path: str, ref: str = None) -> str:
        revision = ref or "HEAD"
        target = self._build_file_target(repo_name, file_path, revision)
        result = subprocess.run(
            [self.svn_cmd, "cat"] + self._auth_flags() + [target],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"svn cat failed for {file_path}@{revision}: {result.stderr.decode(errors='replace').strip()}"
            )
        return result.stdout.decode("utf-8", errors="replace")

    def create_review_comment(
        self, repo_name: str, commit: str, file_path: str, line: int, comment: str, side: str
    ):
        # SVN has no inline comment support; log for visibility.
        logger.info(
            "SVN comment output (not posted): %s:%s %s", file_path, line, comment
        )
        print(f"[SVN REVIEW] {file_path}:{line} {comment}")
        return {"file": file_path, "line": line, "comment": comment}

    def get_commit(self, repo_name: str, commit_sha: str) -> Commit:
        log_output = self._run_svn(repo_name, ["log", "-r", str(commit_sha), "-l", "1"])
        message = self._parse_log_message(log_output)

        author = None
        date = None
        for line in log_output.splitlines():
            if line.startswith("r") and "|" in line:
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3:
                    author = parts[1]
                    date = parts[2]
                break

        return Commit(sha=str(commit_sha), message=message, author=author, date=date)
