def get_prompt(deep: bool = False, repository_requirements: str = "") -> str:
    """
    Returns the prompt for the given mode and deep flag, instructing LLM to return JSON output.

    Args:
        mode: The mode ('issues', 'comments').
        deep: Whether deep mode is enabled (verbose feedback).

    Returns:
        The prompt to use for the LLM.
    """
    base_json_schema = (
        "Return exactly one valid JSON value: a top-level JSON array. "
        "The first non-whitespace character of your response must be '[' and the last non-whitespace character must be ']'. "
        "Do not return standalone objects, comma-separated objects outside an array, markdown code fences, comments, explanations, or any text before or after the JSON array. "
        "For one file, still return an array with one object. For multiple files, put all file objects inside the same array. "
        "Each array element must be an object with the following fields:\n"
        "{\n"
        "  \"file\": string, the file path or name,\n"
        "  \"line\": integer, the line number of the issue in the new file from 'Line in new file', or old file from 'Line in old file' for deletions,\n"
        "  \"comments\": array of strings, concise feedback items,\n"
        "  \"bugCount\": integer, total number of bugs detected in this diff,\n"
        "  \"smellCount\": integer, total number of code-smell issues found,\n"
        "  \"optimizationCount\": integer, total number of optimization suggestions,\n"
        "  \"logicalErrors\": integer, total number of logical errors,\n"
        "  \"performanceIssues\": integer, total number of performance issues\n"
        "}\n"
        "Rules:\n"
        "  1. Include one object per file, even if all counts are zero and comments is empty.\n"
        "  2. If a file has no issues, set bugCount, smellCount, optimizationCount, logicalErrors, performanceIssues to 0 and comments to [].\n"
        "  3. If the entire diff is empty or missing, return [] exactly.\n"
        "  4. Output must be valid, parsable JSON: double-quote every key and string, omit trailing commas, and wrap every object in the top-level array.\n"
        "  5. Invalid output example: {\"file\":\"a.py\"},{\"file\":\"b.py\"}. Valid output example: [{\"file\":\"a.py\",\"line\":1,\"comments\":[],\"bugCount\":0,\"smellCount\":0,\"optimizationCount\":0,\"logicalErrors\":0,\"performanceIssues\":0},{\"file\":\"b.py\",\"line\":1,\"comments\":[],\"bugCount\":0,\"smellCount\":0,\"optimizationCount\":0,\"logicalErrors\":0,\"performanceIssues\":0}].\n"
        "  6. Do not report missing trailing newlines or the diff marker '\\ No newline at end of file' as an issue.\n"
        "  7. Do not report minor style-only issues, including trailing whitespace, indentation-only formatting, line length, naming preferences, import ordering, or cosmetic consistency, unless the diff shows a concrete bug, parser error, broken generated output, or explicit repository requirement violation.\n"
        "  8. Keep output brief: include at most 3 comments per file, each comment must be one short sentence of at most 25 words, and each comment must describe one issue only.\n"
        "  9. If there are more than 3 issues in a file, report only the highest-impact bugs, logical errors, or performance issues; omit lower-impact smells and optional improvements.\n"
        "  10. Counts must reflect only the issues included in comments; do not count omitted or intentionally ignored issues.\n"
    )

    requirements_block = ""
    if repository_requirements:
        requirements_block = (
            "Repository-specific review requirements:\n"
            f"{repository_requirements}\n"
            "Treat these as mandatory constraints for this repository.\n"
        )

    if deep:
        return (
            "Review the provided code diffs and identify concrete issues directly introduced by the changed lines, including bugs, real code smells, logical errors, performance issues, and maintainability concerns with clear impact. "
            "Use unchanged surrounding code or full-file context only to understand the changed lines; do not review, praise, or suggest changes for code that is not modified in the diff. "
            "For each file, provide concise feedback only for problems that are directly visible in the diff and are actionable on a modified line. "
            "Use the PR description to understand the intent and do not flag issues if the PR description explains the reasoning behind a change, unless the change introduces a clear bug. "
            "If the changed code is acceptable, return empty comments and zero counts for that file; do not add positive comments such as 'good addition', 'clean improvement', or 'no issues found'. "
            "Do not suggest speculative checks, tests, validations, or hidden-code investigations unless the diff itself shows that a required check is missing; assume the author already performed reasonable checks and testing. "
            "Do not propose optional strengthening or defensive improvements when the current changed code is already good enough. "
            f"{requirements_block}"
            f"{base_json_schema} "
            "For each file, include only concise, high-impact issues in the 'comments' array, referencing the modified lines."
        )
    else:
        return (
            "Review the provided code diffs and identify critical bugs directly visible in the modified lines, such as syntax errors, null-pointer exceptions, or logical errors. "
            "Use the PR description to understand the intent and do not flag issues if the PR description explains the reasoning behind a change, unless the change introduces a clear bug. "
            "Do not provide general suggestions or speculative concerns. "
            f"{requirements_block}"
            f"{base_json_schema} "
            "For each file, include only critical bugs in the 'comments' array, referencing the modified lines."
        )
