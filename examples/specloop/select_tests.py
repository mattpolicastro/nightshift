"""Select direct Python importers; empty output tells the caller to run all tests.

This is only a narrowing hint: indirect and dynamic imports require full-suite
confirmation. Repository code is parsed, never imported or executed.
"""

import argparse
import ast
import keyword
import os
from pathlib import Path


def _module(root: Path, path: Path) -> tuple[str, bool]:
    relative = path.relative_to(root)
    initializer = path.name == "__init__.py"
    parts = relative.with_suffix("").parts
    if initializer:
        parts = parts[:-1]
    if not parts or any(not p.isidentifier() or keyword.iskeyword(p) for p in parts):
        raise ValueError("unresolvable module")
    parent = path.parent
    while parent != root:
        if not (parent / "__init__.py").is_file():
            raise ValueError("not a repository-root package")
        parent = parent.parent
    return ".".join(parts), initializer


def _imports(root: Path, path: Path, tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                module, initializer = _module(root, path)
                package = module.split(".") if initializer else module.split(".")[:-1]
                if node.level > len(package):
                    raise ValueError("unresolvable relative import")
                prefix = package[:len(package) - node.level + 1]
                base = ".".join(prefix + ([base] if base else []))
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names if alias.name != "*")


def select_tests(root: Path, mutant_path: str) -> list[str]:
    """Return sorted repository-relative tests, or [] to request the full suite.

    Support root modules and packages with __init__.py, without guessing src
    layouts. Any unsupported input or scan uncertainty abandons narrowing.
    """
    if not mutant_path:
        return []
    try:
        root = root.resolve()
        path = (root / mutant_path).resolve()
        path.relative_to(root)  # Includes symlink escape protection.
        if path.suffix != ".py" or not path.is_file():
            return []
        module, initializer = _module(root, path)
        tests = root / "tests"
        if not tests.is_dir():
            return []

        def scan_error(error: OSError) -> None:
            raise error

        selected = set()
        for directory, dirs, files in os.walk(tests, onerror=scan_error):
            # A linked tree cannot be scanned reliably without cycle tracking.
            if any((Path(directory) / name).is_symlink() for name in dirs):
                return []
            for name in files:
                if not (name.endswith(".py") and
                        (name.startswith("test_") or name.endswith("_test.py"))):
                    continue
                test = Path(directory) / name
                test.resolve().relative_to(root)
                tree = ast.parse(test.read_bytes(), filename=str(test))
                imports = list(_imports(root, test, tree))
                if any(item == module or (initializer and item.startswith(module + "."))
                       for item in imports):
                    selected.add(test.relative_to(root).as_posix())
        return sorted(selected)
    except (OSError, SyntaxError, UnicodeError, ValueError, RuntimeError):
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mutant_path", nargs="?", default="")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    for test in select_tests(args.root, args.mutant_path):
        print(test)


if __name__ == "__main__":
    main()
