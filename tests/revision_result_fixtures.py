"""Bind simulated successful revisions to the command that actually ran."""

from copy import deepcopy


def revision_success(argv, result):
    """Return an independent success payload; spec and legacy calls stay unkeyed.

    Call only for successful fixtures. Invalid results must bypass this builder
    or be corrupted afterwards, so fixture construction cannot repair failures.
    """
    result = deepcopy(result)
    args = list(argv)
    if args[:1] == ["specloop"]:
        args = args[1:]
    if args[:1] == ["revise"] and "--round-id" in args:
        assert result["outcome"] == "revised" and result["commit"]
        result.update(round_id=args[args.index("--round-id") + 1], replayed=False,
                      status="running", pause=None)
    return result
