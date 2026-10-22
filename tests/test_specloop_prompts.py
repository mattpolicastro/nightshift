from specloop import prompts


def test_implementer_reports_its_own_results_in_the_summary_not_the_repository():
    # PR #27's build prepended its sandbox test counts to docs/validation.md
    # because its task said "record the result".
    assert "Report them in `summary`, never in a repository file" in prompts.IMPLEMENT
    assert '"record\n  the result" means `summary`' in prompts.IMPLEMENT


def test_implementer_does_not_block_on_its_own_sandbox_failures():
    # #14, #8 and #4's builds each saw the same two sandbox-only failures; #14 blocked on them.
    assert "say so in `summary` and still report" in prompts.IMPLEMENT
    assert 'That is not "blocked".' in prompts.IMPLEMENT
