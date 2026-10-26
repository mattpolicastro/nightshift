"""Physical-line command extraction shared by operator documentation tests."""

import argparse
import re
import shlex
from unittest.mock import patch

from nightshift import cli


PREFIX = re.compile(r"^\s*(?:nightshift|specloop|uv run|python -m)(?:\s|$)")
TICK = re.compile(r"(?<!`)`(?!`)")
PLACEHOLDERS = {
    "<change>": "example-change", "<id>": "0123456789abcdef0123456789abcdef",
    "<repo-manifest.toml>": "repo.toml", "<manifest>": "repo.toml",
    "spec/<change>": "spec/example-change", "<url>": "https://github.com/o/r/pull/1",
    "<task>": "1.1", "<text>": "Review feedback", "<file>": "feedback.md",
    "<key>": "review:1", "<verb>": "resume", "NAME": "example-change",
}
OPTIONALS = {
    "[<change>]": ("example-change",), "[task]": ("1.1",),
    '[--note "..."]': ("--note", "Review feedback"),
    "[--note TEXT]": ("--note", "Review feedback"),
    "[--pr <url>]": ("--pr", "https://github.com/o/r/pull/1"),
    "[--source doc]": ("--source", "doc"),
}


def physical_commands(document, *, comments=False):
    for number, original in enumerate(document.splitlines(), 1):
        line = re.sub(r"^(\s*)# ?", r"\1", original, count=1) if comments else original
        parts = TICK.split(line)
        assert len(parts) % 2 == 1 or not any(PREFIX.match(p) for p in parts), (
            number, "unmatched command delimiter", original
        )
        candidates = [part for part in parts[1::2] if PREFIX.match(part)]
        if PREFIX.match(line):
            candidates.append(line.strip())
        for command in candidates:
            yield number, command


def argv_cases(command):
    # No continuations/escapes; only an unquoted, word-start # is a comment.
    # shlex's comments=True would hide composition after an embedded #.
    assert "\\" not in command, command
    command = re.sub(r'''("[^"]*"|'[^']*')|(?<!\S)#.*''',
                     lambda match: match[1] or "", command)
    quoted = re.findall(r'''"[^"]*"|'[^']*' ''', command, re.X)
    assert not any("[" in part or "]" in part for part in quoted), command
    words = shlex.split(command)
    assert not any(re.search(r"[;&|`$\\()]", word) for word in words), command
    forms = [command]
    for optional, replacement in OPTIONALS.items():
        # Only a complete optional group is defined here. Expanding a fragment
        # of an argv word (or quoted value) would silently guess its meaning.
        pattern = re.compile(r"(?<!\S)" + re.escape(optional) + r"(?!\S)")
        forms = [variant for form in forms for variant in (
            (pattern.sub("", form), pattern.sub(shlex.join(replacement), form))
            if pattern.search(form) else (form,)
        )]
    for form in forms:
        argv = [PLACEHOLDERS.get(word, word) for word in shlex.split(form)]
        assert not any(re.search(r"[<>\[\]{}*?]|\.\.\.|^[A-Z][A-Z_]+$", word)
                       for word in argv), command
        if argv[:2] == ["uv", "run"]:
            argv = argv[2:]
            if argv[:1] == ["--no-sync"]:
                argv = argv[1:]
        if argv[:3] in (["python", "-m", "nightshift.cli"], ["python", "-m", "specloop.cli"]):
            argv = ["nightshift", *argv[3:]]
        yield argv


def parse_command(command):
    parser = cli.build_parser()
    choices = next(action.choices for action in parser._actions if action.dest == "cmd")
    parsed = []
    for argv in argv_cases(command):
        assert argv[0] in ("nightshift", "specloop"), argv
        # Bare executable/verb references are vocabulary, not runnable examples.
        if len(argv) == 1:
            continue
        if len(argv) == 2:
            assert argv[1] in choices, argv
            # Removed implicit daemon controls must never pass as references.
            assert argv[1] not in ("pause", "resume"), argv
            if argv[1] not in ("status", "run", "serve", "migrate", "daemon", "jobs",
                               "attention", "preflight", "reconcile", "digest"):
                continue
        parsed.append(parser.parse_args(argv[1:]))
    return parsed


def parse_report(main, argv):
    """Older report CLIs construct their real parser inside main; stop at parsing."""
    original = argparse.ArgumentParser.parse_args

    class Parsed(Exception):
        pass

    def capture(parser, args):
        raise Parsed(original(parser, args))

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            main(argv)
        except Parsed as result:
            return result.args[0]
    raise AssertionError("report did not parse argv")
