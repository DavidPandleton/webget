"""Plugin discovery and dispatch for external webget subcommands.

External commands follow the git/cargo convention:
`webget <subcommand>` -> finds executable `webget-<subcommand>` in
`~/.local/share/webget/plugins/` or in `$PATH`.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PLUGIN_DIR = os.path.expanduser("~/.local/share/webget/plugins")


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_plugin(name: str) -> str | None:
    """Find an executable plugin by subcommand name.

    Returns the absolute path to the plugin executable, or None if not found.
    Rejects names containing path traversal characters.
    """
    if not name or not _PLUGIN_NAME_RE.match(name) or name in (".", ".."):
        return None

    binary_name = f"webget-{name}"

    # 1. Check dedicated plugin directory
    pdir_bin = os.path.join(PLUGIN_DIR, binary_name)
    if _is_executable(pdir_bin):
        return pdir_bin

    # 2. Check system PATH
    found_in_path = shutil.which(binary_name)
    if found_in_path and _is_executable(found_in_path):
        return os.path.abspath(found_in_path)

    return None


def list_plugins() -> list[dict[str, str]]:
    """Enumerate all discovered webget plugins in plugin dir and PATH."""
    discovered: dict[str, str] = {}

    # 1. Plugin dir
    if os.path.isdir(PLUGIN_DIR):
        try:
            for entry in os.listdir(PLUGIN_DIR):
                if entry.startswith("webget-"):
                    full_p = os.path.join(PLUGIN_DIR, entry)
                    if _is_executable(full_p):
                        subcmd = entry[len("webget-") :]
                        discovered[subcmd] = full_p
        except OSError:
            pass

    # 2. PATH
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for pdir in path_dirs:
        if not pdir or not os.path.isdir(pdir):
            continue
        try:
            for entry in os.listdir(pdir):
                if entry.startswith("webget-") and entry not in ("webget-cli", "webget-mcp"):
                    full_p = os.path.join(pdir, entry)
                    if _is_executable(full_p):
                        subcmd = entry[len("webget-") :]
                        if subcmd not in discovered:
                            discovered[subcmd] = full_p
        except OSError:
            pass

    results = []
    for subcmd in sorted(discovered.keys()):
        results.append(
            {
                "name": subcmd,
                "binary": f"webget-{subcmd}",
                "path": discovered[subcmd],
            }
        )
    return results


def dispatch_plugin(name: str, args: list[str]) -> None:
    """Execute plugin replacing current process on POSIX systems."""
    plugin_path = find_plugin(name)
    if not plugin_path:
        return

    argv = [plugin_path] + args
    if hasattr(os, "execvp"):
        try:
            os.execvp(plugin_path, argv)
        except OSError as e:
            print(f"webget: failed to execute plugin '{name}': {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Windows fallback
        import subprocess

        try:
            ret = subprocess.call(argv)
            sys.exit(ret)
        except OSError as e:
            print(f"webget: failed to execute plugin '{name}': {e}", file=sys.stderr)
            sys.exit(1)
