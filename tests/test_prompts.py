from prompts import get_prompt


def test_deep_prompt_limits_feedback_to_changed_lines():
    prompt = get_prompt(deep=True)

    assert "directly introduced by the changed lines" in prompt
    assert "do not review, praise, or suggest changes for code that is not modified in the diff" in prompt
    assert "return empty comments and zero counts" in prompt
    assert "Do not suggest speculative checks" in prompt
    assert "Do not propose optional strengthening" in prompt


def test_deep_prompt_includes_repository_requirements():
    prompt = get_prompt(deep=True, repository_requirements="- MUST: Avoid print statements.")

    assert "Repository-specific review requirements:" in prompt
    assert "- MUST: Avoid print statements." in prompt
    assert "Treat these as mandatory constraints" in prompt


def test_prompt_requires_single_top_level_json_array():
    for prompt in (get_prompt(deep=True), get_prompt(deep=False)):
        assert "Return exactly one valid JSON value: a top-level JSON array" in prompt
        assert "must be '['" in prompt
        assert "must be ']'" in prompt
        assert "Do not return standalone objects, comma-separated objects outside an array" in prompt
        assert "wrap every object in the top-level array" in prompt
        assert "Invalid output example" in prompt
        assert "Valid output example" in prompt


def test_prompt_ignores_missing_trailing_newline_marker():
    for prompt in (get_prompt(deep=True), get_prompt(deep=False)):
        assert "Do not report missing trailing newlines" in prompt
        assert "\\ No newline at end of file" in prompt


def test_prompt_ignores_minor_style_only_issues():
    for prompt in (get_prompt(deep=True), get_prompt(deep=False)):
        assert "Do not report minor style-only issues" in prompt
        assert "trailing whitespace" in prompt
        assert "indentation-only formatting" in prompt
        assert "cosmetic consistency" in prompt

    deep_prompt = get_prompt(deep=True)
    assert "style problems" not in deep_prompt
    assert "maintainability concerns with clear impact" in deep_prompt


def test_prompt_requires_concise_comments():
    for prompt in (get_prompt(deep=True), get_prompt(deep=False)):
        assert "concise feedback items" in prompt
        assert "at most 3 comments per file" in prompt
        assert "one short sentence of at most 25 words" in prompt
        assert "one issue only" in prompt
        assert "report only the highest-impact bugs" in prompt
        assert "Counts must reflect only the issues included in comments" in prompt

    deep_prompt = get_prompt(deep=True)
    assert "provide concise feedback" in deep_prompt
    assert "concise, high-impact issues" in deep_prompt
