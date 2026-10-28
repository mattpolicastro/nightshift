import json
import shutil
import tomllib
from pathlib import Path

import pytest

from specloop import manifest, verify
from test_snapshot_warming import warming  # noqa: F401
from test_specloop_seal_verify import M_DATA

START, END = "<!-- snapshot-example:start -->", "<!-- snapshot-example:end -->"
GUIDE = Path(__file__).resolve().parents[1] / "docs/specloop/prepare.md"


def snapshot_example(text):
    lines = text.splitlines()
    assert lines.count(START) == lines.count(END) == 1
    start, end = lines.index(START), lines.index(END)
    assert start < end
    block = lines[start + 1:end]
    assert len(block) >= 2 and block[0] == "```toml" and block[-1] == "```"
    assert not any("```" in line or "~~~" in line for line in block[1:-1])
    return tomllib.loads("\n".join(block[1:-1]))


@pytest.mark.scenario("specloop-documentation/snapshot-preparation-guidance/operator-configures-a-cache-with-fallback")
@pytest.mark.parametrize("prefix,suffix", [("", ""), ("```html\n", "\n```"), ("<!--\n", "\n-->"), ("    <div>\n", "\n</div>")])
def test_example_uses_global_physical_lines(prefix, suffix):
    example = START + '\n```toml\nx = 1\n```\n' + END
    assert snapshot_example(prefix + example + suffix) == {"x": 1}
    for bad in (example.replace(START, ""), example.replace(END, ""), END + "\n" + START,
                example + "\n" + START, example + "\n" + END,
                example.replace("```toml", "    ```toml"), example.replace("x = 1", "invalid TOML"),
                example.replace("x = 1", "```\nx = 1"), example.replace("x = 1", "    ~~~\nx = 1"),
                example.replace(START, "    " + START), example.replace("```toml", "```")):
        with pytest.raises((AssertionError, tomllib.TOMLDecodeError)):
            snapshot_example(prefix + bad + suffix)


@pytest.mark.scenario("specloop-documentation/snapshot-preparation-guidance/operator-configures-a-cache-with-fallback")
def test_documented_recipe_with_local_standins(warming, tmp_path, monkeypatch):
    repo, commit, _, context, events = warming
    fragment = snapshot_example(GUIDE.read_text())
    manifest.parse({**M_DATA, **fragment})  # Validate the actual documented fields/references.
    dart = tmp_path / "dart"
    dart.write_text('''#!/bin/sh
set -eu
test "$1 $2" = "pub get"
test ! -e .dart_tool/stale
printf '%s:%s\\n' "$(cat source)" "${3:-network}"
if [ "${3:-}" = --offline ]; then test -f "$PUB_CACHE/dependency" || exit 1; fi
cp pubspec.lock "$PUB_CACHE/dependency"
mkdir -p .dart_tool
cat source > .dart_tool/package_config.json
''')
    dart.chmod(0o755)
    fragment["vars"]["dart"] = str(dart)
    m = manifest.parse({**M_DATA, **fragment})
    (repo / "pubspec.lock").write_text("dependency")
    (repo / "source").write_text("base")
    (repo / ".dart_tool").mkdir()
    (repo / ".dart_tool/stale").write_text("absolute base paths")
    base = commit()
    verify.warm_snapshots(repo, base, m, context)
    assert events[-1][0] == "snapshot_warmed"
    assert (Path(events[-1][1]["diagnostics"]) / "warm.log").read_text() == "base:network\n"
    entry, metadata = verify._snapshot_entries(context.root)[0]
    assert metadata["base_sha"] == base and sorted(p.name for p in (entry / "payload").iterdir()) == ["dependency"]
    (repo / "source").write_text("candidate")
    candidate = commit(lock="candidate")
    monkeypatch.setattr(verify, "_clone_api", lambda: lambda src, dst, flags: (shutil.copyfile(src, dst), 0)[1])
    for hit in (True, False):
        out = tmp_path / str(hit)
        store = context if hit else verify.SnapshotContext(tmp_path / "legacy-no-store")
        receipts = verify.run_steps(repo, candidate, m.verify, m, out, snapshots=store)
        assert verify.ok(receipts, m.prepare + m.verify) and [r.id for r in receipts] == ["dependencies", "t"]
        assert (out / "build/project/.dart_tool/package_config.json").read_text() == "candidate"
        assert receipts[0].tail == "candidate:--offline\n" + ("" if hit else "candidate:network\n")
        fallback = [json.loads(line) for line in (out / "snapshot-events.jsonl").read_text().splitlines()]
        assert fallback == [] if hit else len(fallback) == 1 and fallback[0]["reason"] == "missing"
    assert not store.root.exists() and (entry / "payload/dependency").read_text() == "dependency"
