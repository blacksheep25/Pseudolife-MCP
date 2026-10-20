"""Select Rust CI inputs; other Python behavior is checked by nightly parity.

Keep the workflow itself active so its aggregate check can report success for
unrelated changes. PRs compare the tested merge tree with its base parent;
pushes compare the whole pushed range. Missing history runs the checks.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


PREFIXES = (
    "rust/", "evals/rust_port/", "evals/rust_baseline/", "frontend/",
    "pseudolife_memory/web/static/", "plugin/hooks/", "ops/",
)
EXACT_INPUTS = {
    ".github/workflows/rust.yml", ".dockerignore", "pyproject.toml",
    "pseudolife_memory/storage/schema.py", "pseudolife_memory/mcp_server.py",
    "pseudolife_memory/web/api.py", "pseudolife_memory/web/routes.py",
    "pseudolife_memory/coordination.py", "pseudolife_memory/utils/config.py",
    "evals/claude_shim.py", "evals/codex_shim.py",
    "tests/conftest.py", "tests/fake_embedder.py", "tests/suite_lock.py",
    "tests/pg_defaults.py", "tests/pg_fixtures.py", "tests/asgi_helpers.py",
    "tests/test_transfer_cli.py", "tests/test_coordination_audit.py",
    "tests/maintainer_authenticator.py", "tests/test_cli_dispatch.py",
    "evals/memory_policy_bench.py", "evals/memory_policy_daemon.py",
}


def relevant(path: str) -> bool:
    return path in EXACT_INPUTS or path.startswith(PREFIXES)


def required(root: Path, event: str, base: str, head: str) -> bool:
    if event not in ("pull_request", "push") or not base or set(base) == {"0"}:
        return True
    try:
        # No rename detection: moving a Rust input out of scope must still run.
        paths = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z", base, head],
            cwd=root, timeout=60).decode("utf-8").split("\0")
    except (subprocess.SubprocessError, OSError, UnicodeError):
        print("Cannot determine changed inputs; running Rust CI")
        return True
    paths = [path for path in paths if path]
    return not paths or any(relevant(path) for path in paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    base = "HEAD^1" if args.event == "pull_request" else args.base
    needed = required(Path.cwd(), args.event, base, args.head)
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"needed={str(needed).lower()}\n")
    print(f"Rust CI needed: {str(needed).lower()}")


if __name__ == "__main__":
    main()
