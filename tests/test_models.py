from models import CodeReview, LLMReviewResult


def test_from_json_accepts_snake_case_count_fields():
    result = LLMReviewResult.from_json(
        '[{"file":"main.py","line":7,"comments":["Bug found"],'
        '"bug_count":1,"smell_count":2,"optimization_count":3,'
        '"logical_errors":4,"performance_issues":5}]',
        total_tokens=100,
        prompt_tokens=70,
        completion_tokens=30,
    )

    review = result.reviews[0]
    assert review.bug_count == 1
    assert review.smell_count == 2
    assert review.optimization_count == 3
    assert review.logical_errors == 4
    assert review.performance_issues == 5
    assert result.totals["bug_count"] == 1
    assert result.totals["smell_count"] == 2
    assert result.totals["optimization_count"] == 3
    assert result.totals["logical_errors"] == 4
    assert result.totals["performance_issues"] == 5


def test_from_json_counts_comments_as_smells_when_counters_are_missing_or_zero():
    result = LLMReviewResult.from_json(
        '[{"file":"main.py","line":7,"comments":["Issue one","Issue two"]}]',
        total_tokens=100,
        prompt_tokens=70,
        completion_tokens=30,
    )

    review = result.reviews[0]
    assert review.smell_count == 2
    assert result.totals["smell_count"] == 2
    assert result.totals["total_tokens"] == 100


def test_overall_review_reports_no_issues_with_token_usage():
    result = LLMReviewResult(
        reviews=[],
        total_tokens=1664,
        prompt_tokens=1298,
        completion_tokens=366,
    )

    assert result.get_overall_review(False, False, "claude") == (
        "No issues detected by claude. Total tokens: 1664; "
        "Prompt tokens: 1298; Completion tokens: 366."
    )


def test_overall_review_reports_issue_counts_before_token_usage():
    result = LLMReviewResult(
        reviews=[
            CodeReview(
                file="main.py",
                line=1,
                comments=["Issue"],
                bug_count=1,
                smell_count=0,
                optimization_count=0,
                logical_errors=0,
                performance_issues=0,
            )
        ],
        total_tokens=10,
        prompt_tokens=7,
        completion_tokens=3,
    )

    assert result.get_overall_review(True, True, "claude") == (
        "claude review summary (deep) with full context: Bugs found: 1; "
        "Total tokens: 10; Prompt tokens: 7; Completion tokens: 3."
    )
