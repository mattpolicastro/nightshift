from specloop import prompts


def test_implementer_reports_its_own_results_in_the_summary_not_the_repository():
    # PR #27's build prepended its sandbox test counts to docs/validation.md
    # because its task said "record the result".
    assert "Report them in `summary`, never in a repository file" in prompts.IMPLEMENT
    assert '"record\n  the result" means `summary`' in prompts.IMPLEMENT
