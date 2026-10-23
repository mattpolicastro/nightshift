import subprocess
import sys
from pathlib import Path

import pytest

from examples.specloop import select_tests as selector


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repository with spaces"

    def write(path, content=""):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    write("pkg/__init__.py", "raise AssertionError('package executed')\n")
    write("pkg/core.py", "raise AssertionError('module executed')\n")
    write("tests/test_direct.py", "import pkg.core\n")
    return root, write


def cli(root, *args, explicit_root=True):
    result = subprocess.run(
        [sys.executable, "-B", str(Path(selector.__file__).resolve()), *args,
         *(["--root", str(root)] if explicit_root else [])],
        cwd=root.parent if explicit_root else root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout


@pytest.mark.scenario("mutation-selection/python-import-selector/importing-tests-are-selected")
def test_select_tests_finds_importing_test_files(repo):
    root, write = repo
    marker = root / "executed"
    write("pkg/core.py", f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    write("tests/z_test.py", "import pkg.core as alias, unrelated\nfrom pkg.core import value\n")
    write("tests/nested/test_alias.py", "def check():\n    from pkg import core as alias\n")
    write("tests/test_from.py", "from pkg.core import value as alias\nfrom pkg.core import *\n")
    write("tests/test_lookalikes.py", '# import pkg.core\ntext = "from pkg import core"\n')
    write("tests/test_prefix.py", "import pkg.core_extra\nfrom pkg2 import core\nimport pkg\n")
    write("tests/helper.py", "import pkg.core\ninvalid syntax ignored\n")
    write("test_outside.py", "import pkg.core\n")
    write("tests/test_side_effect.py", f"raise AssertionError('test executed')\n"
          f"from pathlib import Path\nPath({str(marker)!r}).touch()\nimport pkg.core\n")
    expected = ["tests/nested/test_alias.py", "tests/test_direct.py", "tests/test_from.py",
                "tests/test_side_effect.py", "tests/z_test.py"]
    assert selector.select_tests(root, "pkg/core.py") == expected
    assert cli(root, "pkg/core.py") == "".join(path + "\n" for path in expected)
    assert cli(root, "pkg/core.py", explicit_root=False).splitlines() == expected
    assert not marker.exists()


@pytest.mark.scenario("mutation-selection/python-import-selector/importing-tests-are-selected")
@pytest.mark.parametrize("mutant, source, expected", [
    ("foo.py", "import foo as alias\n", True),
    ("foo.py", "from foo import value\n", True),
    ("foo.py", "import foobar\nfrom foobar import value\n", False),
    ("pkg/__init__.py", "import pkg\n", True),
    ("pkg/__init__.py", "from pkg import core\n", True),
    ("pkg/__init__.py", "import pkg.deep.child as alias\n", True),
    ("pkg/__init__.py", "from pkg.deep.child import value\n", True),
    ("pkg/__init__.py", "import pkg_extra\nfrom pkg_extra import core\n", False),
])
def test_module_boundaries_and_initializers(repo, mutant, source, expected):
    root, write = repo
    write(mutant)
    write("tests/test_direct.py", source)
    assert selector.select_tests(root, mutant) == (["tests/test_direct.py"] if expected else [])


@pytest.mark.scenario("mutation-selection/python-import-selector/importing-tests-are-selected")
@pytest.mark.parametrize("source", ["from . import helper as alias\n",
                                          "from .helper import value\n"])
def test_relative_imports(repo, source):
    root, write = repo
    write("tests/__init__.py")
    write("tests/helper.py")
    write("tests/test_relative.py", source)
    write("tests/nested/__init__.py")
    write("tests/nested/test_parent.py", "def check():\n    from ..helper import value\n")
    expected = ["tests/nested/test_parent.py", "tests/test_relative.py"]
    assert selector.select_tests(root, "tests/helper.py") == expected
    assert selector.select_tests(root, "tests/__init__.py") == expected
    assert cli(root, "tests/helper.py").splitlines() == expected


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
@pytest.mark.parametrize("mutant", ["", "pkg/core.ts", "pkg", "missing.py", "__init__.py",
                                    "bad-name.py", "src/pkg/core.py", "../outside.py"])
def test_unsupported_inputs(repo, mutant):
    root, write = repo
    if mutant and mutant not in {"pkg", "missing.py"}:
        write(mutant)
    assert selector.select_tests(root, mutant) == []
    assert cli(root, mutant) == ""
    assert cli(root) == ""


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
@pytest.mark.parametrize("linked", [False, True])
def test_escaping_paths(repo, linked):
    root, write = repo
    outside = write("../outside.py")
    path = outside.resolve()
    if linked:
        path = root / "linked.py"
        path.symlink_to(outside.resolve())
    assert selector.select_tests(root, str(path)) == []
    assert cli(root, str(path)) == ""


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
@pytest.mark.parametrize("source", ["def broken(\n", "\x00", "from . import core\n",
                                     "from ...pkg import core\n"])
def test_scan_uncertainty_discards_matches(repo, source):
    root, write = repo
    if source.startswith("from ..."):
        write("tests/__init__.py")
    write("tests/z_test.py", source)
    assert selector.select_tests(root, "pkg/core.py") == []
    assert cli(root, "pkg/core.py") == ""


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
def test_invalid_encoding_and_directory_read_failure(repo, monkeypatch):
    root, write = repo
    invalid = write("tests/z_test.py")
    invalid.write_bytes(b"# coding: utf-8\n\xff\n")
    assert selector.select_tests(root, "pkg/core.py") == []
    assert cli(root, "pkg/core.py") == ""
    invalid.unlink()

    def fail_walk(path, *, onerror):
        onerror(PermissionError("fixture directory read failure"))
        return iter(())

    monkeypatch.setattr(selector.os, "walk", fail_walk)
    assert selector.select_tests(root, "pkg/core.py") == []


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
def test_read_failure_discards_matches(repo, monkeypatch):
    root, write = repo
    unreadable = write("tests/z_test.py", "import pkg.core\n")
    read = Path.read_bytes

    def fail(path):
        if path == unreadable:
            raise PermissionError("fixture read failure")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", fail)
    assert selector.select_tests(root, "pkg/core.py") == []
    # A broken link gives the CLI a portable read failure, even when run as root.
    unreadable.unlink()
    unreadable.symlink_to(root / "missing.py")
    assert cli(root, "pkg/core.py") == ""


@pytest.mark.scenario("mutation-selection/python-import-selector/uncertainty-falls-back-to-all-tests")
@pytest.mark.parametrize("mode", ["absent", "empty", "no-match"])
def test_empty_or_no_direct_matches(repo, mode):
    root, write = repo
    (root / "tests/test_direct.py").unlink()
    if mode == "absent":
        (root / "tests").rmdir()
    elif mode == "no-match":
        write("wrapper.py", "import pkg.core\n")
        write("tests/test_indirect.py", "import wrapper\n")
        write("tests/test_dynamic.py", "__import__('pkg.core')\n"
              "import importlib\nimportlib.import_module('pkg.core')\n")
    assert selector.select_tests(root, "pkg/core.py") == []
    assert cli(root, "pkg/core.py") == ""
