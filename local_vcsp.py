import os
import logging
from pathlib import Path
from vcsp_interface import VCSPInterface, PRFile, PR, Commit
from datetime import datetime
from typing import List
import subprocess

logger = logging.getLogger(__name__)

class LocalVCSP(VCSPInterface):
    def __init__(self, base_path: str = "."):
        self.repo_path = Path(base_path).resolve()
        if not (self.repo_path / ".git").exists():
            raise ValueError(f"Provided path {self.repo_path} is not a Git repository")

    def _run_git(self, args: List[str]) -> str:
        result = subprocess.run(["git"] + args, cwd=self.repo_path, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
        return result.stdout

    def get_files_in_pr(self, repo_name: str = None, pr_number: int = None) -> List[PRFile]:
        # Local version: diff against index (staged) or working tree
        diff_output = self._run_git(["diff", "--unified=0"])
        return self._parse_diff(diff_output)

    def _parse_diff(self, diff_text: str) -> List[PRFile]:
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

    def get_file_content(self, repo_name: str, file_path: str, ref: str = "HEAD"):
        path = self.repo_path / file_path
        if not path.exists():
            logger.warning("File %s does not exist", path)
            return None
        with path.open("rb") as f:
            content = f.read()
        from types import SimpleNamespace
        return SimpleNamespace(decoded_content=content)

    def create_review_comment(self, repo_name: str, commit: str, file_path: str, line: int, comment: str, side: str):
        logger.info("[REVIEW] %s:%d - %s", file_path, line, comment)
        return {"file": file_path, "line": line, "comment": comment}

    def get_pull_request(self, repo_name: str, pr_number: int) -> PR:        
        return PR(title="Local Review", body="Uncommitted local changes", head_sha="HEAD", state="OPEN")

    def get_commit(self, repo_name: str, commit_sha: str) -> Commit:
        if commit_sha == "HEAD":
            message = self._run_git(["log", "-1", "--pretty=%B", commit_sha]).strip()
            author = self._run_git(["log", "-1", "--pretty=%an", commit_sha]).strip()
            date = self._run_git(["log", "-1", "--pretty=%ad", commit_sha]).strip()
            return Commit(sha=commit_sha, message=message, author=author, date=date)

    def get_repository(self, repo_name: str):
        return self.repo_path.name
