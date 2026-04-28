import logging
import fnmatch
import json
from typing import Any, Dict, Iterable, List

from config import LOG_CHAR_LIMIT, MAX_LENGTH_DIFF, MAX_TOTAL_LENGTH
from json_cleaner import JsonResponseCleaner
from llm_interface import LLMInterface
from prompts import get_prompt
from models import CodeReview, LLMReviewResult
import re

from vcsp_interface import VCSPInterface
import yaml

DEFAULT_RULES_FILE = ".ai-reviewer.yml"
JSON_PARSE_RESPONSE_LOG_LIMIT = 10000

def _iter_diff_lines(diff_lines: Any) -> Iterable[str]:
    if diff_lines is None:
        return []
    if isinstance(diff_lines, str):
        return diff_lines.splitlines()
    return diff_lines

def _truncate_for_log(text: str, limit: int = JSON_PARSE_RESPONSE_LOG_LIMIT) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n... [truncated {omitted} chars]"

def _format_json_error_context(json_text: str, error: json.JSONDecodeError) -> str:
    lines = json_text.splitlines() or [json_text]
    line = lines[error.lineno - 1] if 0 < error.lineno <= len(lines) else ""
    start_col = max(error.colno - 80, 1)
    end_col = min(error.colno + 80, len(line) + 1)
    excerpt = line[start_col - 1:end_col - 1]
    caret_offset = max(error.colno - start_col, 0)
    return (
        f"line {error.lineno}, column {error.colno}, char {error.pos}:\n"
        f"{excerpt}\n"
        f"{' ' * caret_offset}^"
    )

def _empty_review_result_for_files(
        file_names: List[str], total_tokens: int, prompt_tokens: int, completion_tokens: int) -> LLMReviewResult:
    review_result = LLMReviewResult(
        reviews=[
            CodeReview(
                file=file_name,
                line=1,
                comments=[],
                bug_count=0,
                smell_count=0,
                optimization_count=0,
                logical_errors=0,
                performance_issues=0,
            )
            for file_name in file_names
        ],
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    review_result.totals.update({
        "bug_count": 0,
        "smell_count": 0,
        "optimization_count": 0,
        "logical_errors": 0,
        "performance_issues": 0,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    })
    return review_result

def remove_hunk_counts(diff_text: str) -> str:
    """
    Given a unified diff as a string, remove the comma+count parts
    from hunk header lines:
      @@ -start,count +start,count @@
    becomes
      @@ -start +start @@
    """
    # This regex finds hunk headers, capturing the two start-line numbers
    pattern = re.compile(r'@@ -(\d+),\d+ \+(\d+),\d+ @@')
    # Replace each match with commas removed
    return pattern.sub(r'@@ -\1 +\2 @@', diff_text)

def is_new_file(diff_lines):
    """
    Given a unified diff string or its lines for one file,
    return True if it’s a brand-new file.
    """
    for line in _iter_diff_lines(diff_lines):
        # Git’s explicit marker
        if line.startswith('new file mode '):
            return True
        # Or the /dev/null trick
        if line.startswith('--- ') and '/dev/null' in line:
            return True
    return False

def is_deleted_file(diff_lines):
    """
    Given a unified diff string or its lines for one file,
    return True if it’s a deleted file.
    """
    for line in _iter_diff_lines(diff_lines):
        # Git’s explicit marker
        if line.startswith('deleted file mode '):
            return True
        # Or the /dev/null trick on the new side
        if line.startswith('+++ ') and '/dev/null' in line:
            return True
    return False


class LLMCodeReviewer:
    """Handles code review generation by constructing prompts and parsing LLM JSON responses."""

    def __init__(
            self,
            llm: LLMInterface,
            vcsp: VCSPInterface,  # VCS interface (e.g., GithubVCSP); type depends on implementation
            full_context: bool = False,
            deep: bool = False,
            rules_file: str = DEFAULT_RULES_FILE
    ):
        self.llm = llm
        self.vcsp = vcsp
        self.full_context = full_context
        self.deep = deep
        self.rules_file = rules_file
        self.json_cleaner = JsonResponseCleaner()

    def _to_text(self, file_content: Any) -> str:
        if file_content is None:
            return None
        if isinstance(file_content, str):
            return file_content
        if isinstance(file_content, bytes):
            return file_content.decode("utf-8")

        decoded_content = getattr(file_content, "decoded_content", None)
        if isinstance(decoded_content, bytes):
            return decoded_content.decode("utf-8")
        if isinstance(decoded_content, str):
            return decoded_content

        raise ValueError("Unsupported file content type returned by VCSP")

    def _normalize_path(self, file_path: str) -> str:
        normalized = file_path.replace("\\", "/")
        # Only trim explicit relative prefixes, preserving meaningful
        # leading dots (e.g. ".env") and absolute roots ("/...").
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def _normalize_glob_pattern(self, pattern: str) -> str:
        return self._normalize_path(pattern)

    def _load_review_rules(self, repository: str, head_sha: str, pr_files: List[Any]) -> Dict[str, Any]:
        changed_files = {
            self._normalize_path(file.filename)
            for file in pr_files
            if getattr(file, "filename", None)
        }
        normalized_rules_file = self._normalize_path(self.rules_file)
        source = "PR version" if normalized_rules_file in changed_files else "repository version"

        try:
            raw_content = self.vcsp.get_file_content(repository, self.rules_file, ref=head_sha)
            config_text = self._to_text(raw_content)
        except Exception:
            logging.debug("No repository review rules file found at %s", self.rules_file)
            return {"ignore_paths": [], "global_must": [], "global_avoid": [], "path_rules": []}

        if not config_text:
            return {"ignore_paths": [], "global_must": [], "global_avoid": [], "path_rules": []}

        try:
            config_data = yaml.safe_load(config_text) or {}
        except yaml.YAMLError as e:
            logging.warning("Failed to parse %s: %s", self.rules_file, str(e))
            return {"ignore_paths": [], "global_must": [], "global_avoid": [], "path_rules": []}

        if not isinstance(config_data, dict):
            logging.warning("Invalid %s format: root must be an object", self.rules_file)
            return {"ignore_paths": [], "global_must": [], "global_avoid": [], "path_rules": []}

        review_cfg = config_data.get("review", config_data)
        if not isinstance(review_cfg, dict):
            logging.warning("Invalid %s format: 'review' must be an object", self.rules_file)
            return {"ignore_paths": [], "global_must": [], "global_avoid": [], "path_rules": []}

        def _as_str_list(value: Any) -> List[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        path_rules = []
        for rule in review_cfg.get("path_rules", []):
            if not isinstance(rule, dict):
                continue
            paths = _as_str_list(rule.get("paths", []))
            if not paths:
                continue
            path_rules.append({
                "paths": [self._normalize_glob_pattern(path) for path in paths],
                "must": _as_str_list(rule.get("must", [])),
                "avoid": _as_str_list(rule.get("avoid", [])),
            })

        logging.info("Loaded repository review rules from %s (%s)", self.rules_file, source)
        return {
            "ignore_paths": [self._normalize_glob_pattern(pattern)
                              for pattern in _as_str_list(review_cfg.get("ignore_paths", []))],
            "global_must": _as_str_list(review_cfg.get("global_must", [])),
            "global_avoid": _as_str_list(review_cfg.get("global_avoid", [])),
            "path_rules": path_rules,
        }

    def _is_ignored(self, file_path: str, ignore_patterns: List[str]) -> bool:
        normalized_path = self._normalize_path(file_path)
        return any(fnmatch.fnmatch(normalized_path, pattern) for pattern in ignore_patterns)

    def _build_repository_requirements_prompt(
            self, rules: Dict[str, Any], changed_files: List[str]
    ) -> str:
        lines = []

        for item in rules.get("global_must", []):
            lines.append(f"- MUST: {item}")
        for item in rules.get("global_avoid", []):
            lines.append(f"- AVOID: {item}")

        normalized_changed_files = [self._normalize_path(path) for path in changed_files]
        for path_rule in rules.get("path_rules", []):
            paths = path_rule.get("paths", [])
            applies = any(
                any(fnmatch.fnmatch(file_path, pattern) for pattern in paths)
                for file_path in normalized_changed_files
            )
            if not applies:
                continue
            lines.append(f"- PATHS: {', '.join(paths)}")
            for item in path_rule.get("must", []):
                lines.append(f"  MUST: {item}")
            for item in path_rule.get("avoid", []):
                lines.append(f"  AVOID: {item}")

        return "\n".join(lines)

    def review_pr(self, pr: Any, repository: str, pr_number: int) -> LLMReviewResult:
        """
        Generate a code review for the given PR, returning JSON-based results.

        Args:
            pr: The pull request object from the VCS.
            repository: The repository name (e.g., 'username/repo').
            pr_number: The pull request number.

        Returns:
            LLMReviewResult containing the parsed reviews with adjusted line numbers.
        """        
        retry_count = 0
        while retry_count < 2:
            retry_count += 1
            # Prepare PR title and description
            pr_title = pr.title or "No title provided"
            pr_description = pr.body or "No description provided"
            base_content = f"PR Title: {pr_title}\nPR Description:\n{pr_description}\n\n"

            # Prepare content based on full-context flag
            pr_files = self.vcsp.get_files_in_pr(repository, pr_number)
            if not pr_files:
                logging.info("No changed files to review. Skipping LLM API call.")
                return None
            rules = self._load_review_rules(repository, pr.head_sha, pr_files)
            reviewable_pr_files = []
            for file in pr_files:
                if self._is_ignored(file.filename, rules["ignore_paths"]):
                    logging.info("Skipping ignored file: %s", file.filename)
                    continue
                reviewable_pr_files.append(file)

            if not reviewable_pr_files:
                logging.info("All changed files are ignored. Skipping LLM API call.")
                return None
            
            all_content = [] 
            reviewed_files = []
            all_content_length = 0
            for file in reviewable_pr_files:
                if file.patch and len(file.patch) <= MAX_LENGTH_DIFF:
                    file.patch = remove_hunk_counts(file.patch)
                    if self.full_context and not is_new_file(file.patch) and not is_deleted_file(file.patch):                    
                        try:
                            file_content_raw = self.vcsp.get_file_content(repository, file.filename, ref=pr.head_sha)
                            file_content = self._to_text(file_content_raw)
                            file_chunk = f"File: {file.filename}\n{file_content}\n\nDiff:\n{file.patch}"
                        except ValueError as e:
                            logging.error(f"Skipping file {file.filename}: {str(e)}")

                    else:
                        file_chunk = f"File: {file.filename}\nDiff:\n{file.patch}"
                    all_content.append(file_chunk)
                    reviewed_files.append(file.filename)
                    all_content_length += len(file_chunk)
                    if all_content_length > MAX_TOTAL_LENGTH:
                        logging.warning(f"Content length exceeded {MAX_LENGTH_DIFF} characters. Truncating.")
                        break


            diff_content = "\n\n".join(all_content)

            # Combine PR title, description, and diffs
            content = base_content + "Diffs:\n" + diff_content

            # Get system prompt
            repository_requirements_prompt = self._build_repository_requirements_prompt(
                rules, [file.filename for file in reviewable_pr_files]
            )
            system_prompt = get_prompt(self.deep, repository_requirements_prompt)
            # Call LLM
            llm_answer = self.llm.answer(
                                system_prompt=system_prompt,
                                user_prompt="",  # No separate user prompt needed; content includes all info
                                content=content
                            ) if all_content_length > 0 else None

            if all_content_length <= 0:
                logging.info("No reviewable diff content remained after filtering. Skipping LLM API call.")
                return None

            if llm_answer:
                if llm_answer.response == "Long_Request" and self.full_context:
                    self.full_context = False #retrun with less context
                    logging.warning("LLM response indicates request was too long; retrying with less context.")
                    continue  # Retry with reduced context
                retry_count = 2  # Exit retry loop if we got a valid response
                # Parse JSON response
                cleaned_response = self.json_cleaner.strip(llm_answer.response)
                if not cleaned_response:
                    logging.error("Error: No valid JSON found in LLM response")
                    logging.error(
                        "Raw LLM response that could not be cleaned as JSON (%d chars):\n%s",
                        len(llm_answer.response),
                        _truncate_for_log(llm_answer.response)
                    )
                    return None
                logging.debug(f"Cleaned Response:\n{cleaned_response[:LOG_CHAR_LIMIT]}... (truncated)")
                try:
                    review_result = LLMReviewResult.from_json(cleaned_response, 
                        llm_answer.total_tokens,llm_answer.prompt_tokens, llm_answer.completion_tokens)
                    if not review_result.reviews and reviewed_files:
                        review_result = _empty_review_result_for_files(
                            reviewed_files,
                            llm_answer.total_tokens,
                            llm_answer.prompt_tokens,
                            llm_answer.completion_tokens,
                        )
                    return review_result
                except ValueError as e:
                    logging.error(f"Error parsing LLM response: {str(e)}")
                    if isinstance(e.__cause__, json.JSONDecodeError):
                        logging.error(
                            "JSON parse error near %s",
                            _format_json_error_context(cleaned_response, e.__cause__)
                        )
                    logging.error(
                        "Cleaned LLM response that failed JSON parsing (%d chars):\n%s",
                        len(cleaned_response),
                        _truncate_for_log(cleaned_response)
                    )
            return None
