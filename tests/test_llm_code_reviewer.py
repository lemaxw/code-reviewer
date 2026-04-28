import pytest
from unittest.mock import Mock
from llm_code_reviewer import LLMCodeReviewer, is_deleted_file, is_new_file, remove_hunk_counts
from models import LLMReviewResult, CodeReview
from llm_interface import LLMInterface, ModelResult
from vcsp_interface import PR, PRFile
from pathlib import Path
import logging

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)
TEST_DATA_PATH = Path(__file__).parent / "data"

# Fixture for mocked VCS and LLM
@pytest.fixture
def mock_vcsp(mocker):
    vcsp = Mock()
    return vcsp


@pytest.fixture
def mock_llm(mocker):
    llm = Mock(spec=LLMInterface)
    return llm


# Fixture for sample PR
@pytest.fixture
def sample_pr():
    return PR(title="Test PR", body="Description", head_sha="abc123", state="open")


def test_review_pr_success(mock_vcsp, mock_llm, sample_pr, tmp_path, mocker):
    diff_file = tmp_path / "sample_diff.txt"
    diff_content = """--- a/main.py
+++ b/main.py
@@ -40,6 +40,7 @@
    def process_data(data):
        obj = data.get("object")
        result = obj.method()
+    logger.info("Processed data")
        return result"""
    diff_file.write_text(diff_content, encoding='utf-8')
    logging.debug(f"Created diff file: {diff_file}")
    assert diff_file.exists(), f"Diff file not created: {diff_file}"

    content_file = tmp_path / "sample_file.py"
    content = """import logging

logger = logging.getLogger(__name__)

def process_data(data):
    obj = data.get("object")
    result = obj.method()
    return result"""
    content_file.write_text(content, encoding='utf-8')
    logging.debug(f"Created content file: {content_file}")
    assert content_file.exists(), f"Content file not created: {content_file}"

    mock_file = PRFile(filename="main.py", patch=diff_file.read_text(encoding='utf-8'))
    mock_vcsp.get_files_in_pr.return_value = [mock_file]
    mock_vcsp.get_file_content.return_value = content_file.read_text(encoding='utf-8')
    mock_llm.answer.return_value = ModelResult(response='''[
        {"file": "main.py", "line": 43, "comments": ["Add logging"]}
    ]''', total_tokens=0, prompt_tokens=0, completion_tokens=0)
    mocker.patch("llm_code_reviewer.get_prompt", return_value="Review prompt")
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip",
                 return_value='[{"file": "main.py", "line": 43, "comments": ["Add logging"]}]')
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp, full_context=True, deep=True)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)
    assert isinstance(result, LLMReviewResult)
    assert len(result.reviews) == 1
    assert result.reviews[0].file == "main.py"
    assert result.reviews[0].line == 43  # Updated: + line is at 43
    assert result.reviews[0].comments == ["Add logging"]
    mock_llm.answer.assert_called_once()
    mock_vcsp.get_file_content.assert_called_with("user/repo", "main.py", ref="abc123")


# def test_get_file_line_from_diff(mock_vcsp, tmp_path):
#     diff_file = tmp_path / "sample_diff.txt"
#     diff_content = """--- a/main.py
# +++ b/main.py
# @@ -40,6 +40,7 @@
#     def process_data(data):
#         obj = data.get("object")
#         result = obj.method()
# +    logger.info("Processed data")
#         return result"""
#     diff_file.write_text(diff_content, encoding='utf-8')
#     logging.debug(f"Created diff file: {diff_file}")
#     assert diff_file.exists(), f"Diff file not created: {diff_file}"

#     reviewer = LLMCodeReviewer(llm=Mock(), vcsp=mock_vcsp)
#     line = reviewer._get_file_line_from_diff(diff_file.read_text(encoding='utf-8'))
#     assert line == 43  # Updated: + line is at 43


def test_review_pr_deleted_file(mock_vcsp, mock_llm, sample_pr, tmp_path, mocker):
    diff_file = tmp_path / "deleted_file_diff.txt"
    diff_content = """--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def old_function():
-    print("Old code")
-    return True"""
    diff_file.write_text(diff_content, encoding='utf-8')
    logging.debug(f"Created diff file: {diff_file}")
    assert diff_file.exists(), f"Diff file not created: {diff_file}"

    mock_file = PRFile(filename="old.py", patch=diff_file.read_text(encoding='utf-8'))
    mock_vcsp.get_files_in_pr.return_value = [mock_file]
    mock_llm.answer.return_value =  ModelResult('''[
        {"file": "old.py", "line": 1, "comments": ["File deleted"]}
    ]''', total_tokens=0, prompt_tokens=0, completion_tokens=0)
    mocker.patch("llm_code_reviewer.get_prompt", return_value="Review prompt")
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip",
                 return_value='[{"file": "old.py", "line": 1, "comments": ["File deleted"]}]')
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)
    assert len(result.reviews) == 1
    assert result.reviews[0].file == "old.py"
    assert result.reviews[0].line == 1
    assert result.reviews[0].comments == ["File deleted"]


def test_is_deleted_file_accepts_diff_string():
    diff_content = """diff --git a/.ci.yml b/.ci.yml
deleted file mode 100644
index cb51a6a..0000000
--- a/.ci.yml
+++ /dev/null
@@ -1 +0,0 @@
-kind: application
"""

    assert is_deleted_file(diff_content)


def test_review_pr_deleted_file_with_full_context_does_not_fetch_deleted_content(
        mock_vcsp, mock_llm, sample_pr, mocker):
    diff_content = """diff --git a/old.py b/old.py
deleted file mode 100644
index 1234567..0000000
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old_function():
-    return True
"""
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename="old.py", patch=diff_content)]

    def get_file_content_side_effect(repo_name, file_path, ref=None):
        if file_path == ".ai-reviewer.yml":
            raise FileNotFoundError(file_path)
        raise AssertionError(f"Deleted file content should not be fetched: {file_path}")

    mock_vcsp.get_file_content.side_effect = get_file_content_side_effect
    mock_llm.answer.return_value = ModelResult('''[
        {"file": "old.py", "line": 1, "comments": ["File deleted"]}
    ]''', total_tokens=0, prompt_tokens=0, completion_tokens=0)
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip",
                 return_value='[{"file": "old.py", "line": 1, "comments": ["File deleted"]}]')
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp, full_context=True)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert len(result.reviews) == 1
    mock_vcsp.get_file_content.assert_called_once_with("user/repo", ".ai-reviewer.yml", ref="abc123")


def test_review_pr_new_file(mock_vcsp, mock_llm, sample_pr, tmp_path, mocker):
    diff_file = tmp_path / "new_file_diff.txt"
    diff_content = """--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+def new_function():
+    print("New code")
+    return True"""
    diff_file.write_text(diff_content, encoding='utf-8')
    logging.debug(f"Created diff file: {diff_file}")
    assert diff_file.exists(), f"Diff file not created: {diff_file}"

    mock_file = PRFile(filename="new.py", patch=diff_file.read_text(encoding='utf-8'))
    mock_vcsp.get_files_in_pr.return_value = [mock_file]
    mock_llm.answer.return_value =  ModelResult('''[
        {"file": "new.py", "line": 1, "comments": ["New file added"]}
    ]''', total_tokens=0, prompt_tokens=0, completion_tokens=0)
    mocker.patch("llm_code_reviewer.get_prompt", return_value="Review prompt")
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip",
                 return_value='[{"file": "new.py", "line": 1, "comments": ["New file added"]}]')
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)
    assert len(result.reviews) == 1
    assert result.reviews[0].file == "new.py"
    assert result.reviews[0].line == 1
    assert result.reviews[0].comments == ["New file added"]


def test_is_new_file_accepts_diff_string():
    diff_content = """diff --git a/new.py b/new.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def new_function():
+    return True
"""

    assert is_new_file(diff_content)


def test_remove_hunk_counts():

    sample = """\
@@ -68,7 +68,7 @@ const FOUR_MEGA_BYTES = 4194304;
@@ -101,7 +101,7 @@ export async function main() {
@@ -132,11 +132,12 @@ export async function main() {
"""
    assert remove_hunk_counts(sample) == """\
@@ -68 +68 @@ const FOUR_MEGA_BYTES = 4194304;
@@ -101 +101 @@ export async function main() {
@@ -132 +132 @@ export async function main() {
"""


def test_review_pr_all_files_ignored_skips_llm(mock_vcsp, mock_llm, sample_pr):
    diff_content = """--- a/docs/readme.md
+++ b/docs/readme.md
@@ -1,2 +1,2 @@
-Old text
+New text
"""
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename="docs/readme.md", patch=diff_content)]
    mock_vcsp.get_file_content.return_value = """review:
  ignore_paths:
    - "docs/**"
"""
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp, full_context=False, deep=False)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert result is None
    mock_llm.answer.assert_not_called()


def test_review_pr_loads_rules_file_from_pr_head_sha(mock_vcsp, mock_llm, sample_pr, mocker):
    diff_content = """--- a/main.py
+++ b/main.py
@@ -1,1 +1,1 @@
-print("old")
+print("new")
"""
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename="main.py", patch=diff_content)]

    def get_file_content_side_effect(repo_name, file_path, ref=None):
        if file_path == ".ai-reviewer.yml":
            assert ref == "abc123"
            return """review:
  global_must:
    - "Flag print usage in backend files."
"""
        if file_path == "main.py":
            return "print('new')"
        return ""

    mock_vcsp.get_file_content.side_effect = get_file_content_side_effect
    mock_llm.answer.return_value = ModelResult(
        response='[{"file":"main.py","line":1,"comments":[]}]',
        total_tokens=0,
        prompt_tokens=0,
        completion_tokens=0
    )
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip",
                 return_value='[{"file":"main.py","line":1,"comments":[]}]')

    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp, full_context=True, deep=False)
    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert isinstance(result, LLMReviewResult)
    mock_vcsp.get_file_content.assert_any_call("user/repo", ".ai-reviewer.yml", ref="abc123")
    mock_llm.answer.assert_called_once()


def test_review_pr_normalizes_empty_llm_response_to_zero_count_reviews(
        mock_vcsp, mock_llm, sample_pr, mocker):
    main_diff = """--- a/main.py
+++ b/main.py
@@ -1,1 +1,1 @@
-print("old")
+print("new")
"""
    util_diff = """--- a/util.py
+++ b/util.py
@@ -1,1 +1,1 @@
-value = 1
+value = 2
"""
    mock_vcsp.get_files_in_pr.return_value = [
        PRFile(filename="main.py", patch=main_diff),
        PRFile(filename="util.py", patch=util_diff),
    ]
    mock_vcsp.get_file_content.side_effect = FileNotFoundError
    mock_llm.answer.return_value = ModelResult(
        response='[]',
        total_tokens=10,
        prompt_tokens=7,
        completion_tokens=3
    )
    mocker.patch("llm_code_reviewer.JsonResponseCleaner.strip", return_value='[]')
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert isinstance(result, LLMReviewResult)
    assert [review.file for review in result.reviews] == ["main.py", "util.py"]
    assert all(review.line == 1 for review in result.reviews)
    assert all(review.comments == [] for review in result.reviews)
    assert all(review.bug_count == 0 for review in result.reviews)
    assert all(review.smell_count == 0 for review in result.reviews)
    assert all(review.optimization_count == 0 for review in result.reviews)
    assert all(review.logical_errors == 0 for review in result.reviews)
    assert all(review.performance_issues == 0 for review in result.reviews)
    assert result.totals["total_tokens"] == 10
    assert result.totals["prompt_tokens"] == 7
    assert result.totals["completion_tokens"] == 3
    assert result.totals["bug_count"] == 0
    assert result.totals["smell_count"] == 0
    assert result.totals["optimization_count"] == 0
    assert result.totals["logical_errors"] == 0
    assert result.totals["performance_issues"] == 0


def test_review_pr_hidden_file_ignore_pattern(mock_vcsp, mock_llm, sample_pr):
    diff_content = """--- a/.env
+++ b/.env
@@ -1,1 +1,1 @@
-SECRET=old
+SECRET=new
"""
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename=".env", patch=diff_content)]
    mock_vcsp.get_file_content.return_value = """review:
  ignore_paths:
    - ".env"
"""
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert result is None
    mock_llm.answer.assert_not_called()


@pytest.mark.parametrize("pattern", ["./docs/**", "docs\\**"])
def test_review_pr_normalizes_ignore_glob_patterns(mock_vcsp, mock_llm, sample_pr, pattern):
    diff_content = """--- a/docs/readme.md
+++ b/docs/readme.md
@@ -1,1 +1,1 @@
-old
+new
"""
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename="docs/readme.md", patch=diff_content)]
    mock_vcsp.get_file_content.return_value = f"""review:
  ignore_paths:
    - '{pattern}'
"""
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert result is None
    mock_llm.answer.assert_not_called()


def test_review_pr_no_changed_files_skips_llm(mock_vcsp, mock_llm, sample_pr):
    mock_vcsp.get_files_in_pr.return_value = []
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert result is None
    mock_llm.answer.assert_not_called()
    mock_vcsp.get_file_content.assert_not_called()


def test_review_pr_logs_invalid_json_details(mock_vcsp, mock_llm, sample_pr, caplog):
    diff_content = """--- a/main.py
+++ b/main.py
@@ -1,1 +1,1 @@
-print("old")
+print("new")
"""
    bad_json = (
        '[{"file":"main.py","line":1,"comments":[],"bugCount":0,'
        '"smellCount":0,"optimizationCount":0,"logicalErrors":0,'
        '"performanceIssues":0}]\n'
        '[{"file":"other.py","line":1,"comments":[]}]'
    )
    mock_vcsp.get_files_in_pr.return_value = [PRFile(filename="main.py", patch=diff_content)]
    mock_vcsp.get_file_content.return_value = ""
    mock_llm.answer.return_value = ModelResult(
        response=bad_json,
        total_tokens=0,
        prompt_tokens=0,
        completion_tokens=0
    )
    reviewer = LLMCodeReviewer(llm=mock_llm, vcsp=mock_vcsp)

    with caplog.at_level(logging.ERROR):
        result = reviewer.review_pr(sample_pr, "user/repo", 1)

    assert result is None
    assert "Error parsing LLM response: Invalid JSON response: Extra data" in caplog.text
    assert "JSON parse error near line 2, column 1" in caplog.text
    assert "Cleaned LLM response that failed JSON parsing" in caplog.text
    assert '[{"file":"other.py","line":1,"comments":[]}]' in caplog.text
