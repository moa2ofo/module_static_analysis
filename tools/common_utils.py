#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""

Shared utilities for:
- swCmpBuildCheck.py
- swCmpDocsGenerator.py
- unitTestsLauncher.py

Includes:
- logging helpers
- configurable preflight checks
- robust subprocess runner with safe decoding (Windows-friendly)
- docker mount path conversion (cross-platform)
- safe file helpers (unlink/restore/backup)
- target discovery helpers
- folder copy/clear helpers
- summary helpers
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Iterable, Sequence, List, Tuple, Dict, Any


# -------------------------
# Logging helpers
# -------------------------
def info(msg: str):  print(f"[INFO] {msg}")
def warn(msg: str):  print(f"[WARNING] {msg}")
def error(msg: str): print(f"[ ++++++++++++++++++++++++++++++++++  ERROR  ++++++++++++++++++++++++++++++++++] {msg}")
def fatal(msg: str, code: int = 1):
    print(f"[FATAL] {msg}")
    sys.exit(code)


# -------------------------
# Requirements primitives
# -------------------------
def require_python(min_major: int = 3, min_minor: int = 8):
    if sys.version_info < (min_major, min_minor):
        fatal(
            f"Python {min_major}.{min_minor}+ required. "
            f"Found {sys.version_info.major}.{sys.version_info.minor}"
        )

def require_command(cmd: str):
    if shutil.which(cmd) is None:
        fatal(f"Required command not found in PATH: '{cmd}'")

def require_file(path: Path, desc: str = "File"):
    if not path.is_file():
        fatal(f"{desc} not found: {path}")

def require_dir(path: Path, desc: str = "Directory"):
    if not path.is_dir():
        fatal(f"{desc} not found: {path}")



def run_cmd(
    cmd: list[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    stopScript: bool = True
) -> subprocess.CompletedProcess:
    """
    Run a command capturing output as bytes and decoding safely.
    Prevents UnicodeDecodeError on Windows (cp1252).

    Behavior:
    - If stopScript == True:
        - raise on failures (like before)
    - If stopScript == False:
        - NEVER raise (even if command fails or cannot be executed)
        - return a CompletedProcess with returncode != 0 and stderr set
    """
    info("Running: " + " ".join(cmd) + (f" (cwd={cwd})" if cwd else ""))

    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True  # Always capture, even if stopScript == False
        )

    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]} (is it installed and in PATH?)"
        error(msg)
        if stopScript:
            fatal(msg)

        # Return a fake CompletedProcess object instead of raising
        p = subprocess.CompletedProcess(cmd, returncode=127, stdout=b"", stderr=msg.encode("utf-8"))

    except PermissionError as e:
        msg = f"Permission denied running: {' '.join(cmd)}\nDetails: {e}"
        error(msg)
        if stopScript:
            fatal(msg)

        p = subprocess.CompletedProcess(cmd, returncode=126, stdout=b"", stderr=msg.encode("utf-8"))

    except Exception as e:
        msg = f"Unexpected error while running: {' '.join(cmd)}\nDetails: {repr(e)}"
        error(msg)
        if stopScript:
            fatal(msg)

        p = subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=msg.encode("utf-8"))

    stdout = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    stderr = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""

    # Attach decoded outputs (your convenience API)
    p.stdout_text = stdout  # type: ignore[attr-defined]
    p.stderr_text = stderr  # type: ignore[attr-defined]

    # ✅ IMPORTANT CHANGE:
    # If stopScript == False => do not raise even if check=True and returncode != 0
    if check and p.returncode != 0:
        error(f"Command failed with exit code {p.returncode}: {' '.join(cmd)}")
        if stdout.strip():
            print("---- STDOUT ----")
            print(stdout)
        if stderr.strip():
            print("---- STDERR ----")
            print(stderr)
        if stopScript:
            raise subprocess.CalledProcessError(p.returncode, cmd, output=stdout, stderr=stderr)

    return p


# -------------------------
# Docker helpers
# -------------------------
def require_docker_running():
    """Checks that Docker daemon is accessible."""
    require_command("docker")
    try:
        run_cmd(["docker", "info"], check=True)
    except Exception:
        fatal(
            "Docker is installed but not running or not accessible.\n"
            "Start Docker Desktop / Docker daemon and ensure you have permissions."
        )

def docker_mount_path(p: Path) -> str:
    """
    Convert a local path to a Docker-friendly mount path.
    - Windows: C:\\repo\\proj -> /c/repo/proj
    - Linux/macOS: /home/user/proj -> /home/user/proj
    """
    abs_p = p.resolve()
    if os.name == "nt":
        drive = abs_p.drive.rstrip(":").lower()
        rel = str(abs_p).split(":", 1)[-1].lstrip("\\/").replace("\\", "/")
        return f"/{drive}/{rel}"
    else:
        return str(abs_p)


# -------------------------
# Configurable preflight checks
# -------------------------
def preflight_check(
    *,
    script_dir: Path,
    min_python: tuple[int, int] = (3, 8),
    require_docker: bool = False,
    check_docker_daemon: bool = False,
    required_dirs: Sequence[tuple[Path, str]] = (),
    required_files: Sequence[tuple[Path, str]] = (),
    optional_files: Sequence[tuple[Path, str]] = (),
) -> None:
    """
    Generic preflight check used by multiple scripts.

    Parameters:
      - script_dir: used only for log context
      - min_python: (major, minor)
      - require_docker: check docker in PATH
      - check_docker_daemon: run 'docker info'
      - required_dirs: list of (path, description)
      - required_files: list of (path, description)
      - optional_files: list of (path, description) => warning if missing
    """
    info("Performing preflight checks...")
    require_python(*min_python)

    for p, desc in required_dirs:
        require_dir(p, desc)

    for p, desc in required_files:
        require_file(p, desc)

    for p, desc in optional_files:
        if not p.is_file():
            warn(f"{desc} not found: {p}")

    if require_docker:
        require_command("docker")
    if check_docker_daemon:
        require_docker_running()

    info("Preflight checks OK.")


# -------------------------
# Template resolver
# -------------------------
def resolve_template(script_dir: Path, primary: str, fallback: str) -> Path:
    """
    Resolve a template file in script_dir trying primary then fallback.
    Fatal if none exists.
    """
    p = script_dir / primary
    if p.is_file():
        return p
    p2 = script_dir / fallback
    if p2.is_file():
        return p2
    fatal(f"Template not found. Tried: {primary} and {fallback} in {script_dir}")
    return p  # unreachable


# -------------------------
# Target discovery helpers
# -------------------------
def find_targets_with_subfolders(root: Path, subfolders: Sequence[str] = ("pltf", "cfg")) -> Iterable[Path]:
    """
    Yield directories under `root` that contain at least one of the given subfolders.
    """
    for dirpath, dirnames, _ in os.walk(root):
        dirpath = Path(dirpath)
        for sub in subfolders:
            if sub in dirnames and (dirpath / sub).is_dir():
                yield dirpath
                break


# -------------------------
# Safe file helpers
# -------------------------
def safe_unlink(p: Path):
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        warn(f"Could not remove {p}: {e}")

def safe_restore(backup: Optional[Path], dest: Path):
    if backup and backup.exists():
        try:
            shutil.move(str(backup), str(dest))
        except Exception as e:
            warn(f"Could not restore backup {backup} -> {dest}: {e}")

def backup_if_exists(path: Path, suffix: str = ".bak") -> Optional[Path]:
    """Move existing file to backup and return backup path, else None."""
    if path.exists():
        backup = path.with_name(path.name + suffix)
        try:
            shutil.move(str(path), str(backup))
            return backup
        except Exception as e:
            fatal(f"Could not backup {path} -> {backup}: {e}")
    return None


# -------------------------
# Folder helpers
# -------------------------
def clear_folder(folder_path: Path):
    """Delete all contents of folder_path (folder remains)."""
    if not folder_path.exists():
        warn(f"Folder does not exist: {folder_path}")
        return

    for item in folder_path.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            warn(f"Error deleting '{item}': {e}")

    info(f"Folder cleared: {folder_path}")

def copy_folder_contents(src_folder: Path, dest_folder: Path):
    info(f"Copying from '{src_folder}' to '{dest_folder}'")
    if not src_folder.exists():
        warn(f"Source folder does not exist: '{src_folder}'. Nothing to copy.")
        return

    dest_folder.mkdir(parents=True, exist_ok=True)

    for item in src_folder.iterdir():
        dest_path = dest_folder / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, dest_path, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_path)
        except Exception as e:
            warn(f"Copy failed for '{item}': {e}")


# -------------------------
# Summary helpers
# -------------------------
def print_summary(title: str, ok_items: Sequence[Path], fail_items: Sequence[Tuple[Path, str]]):
    print("\n============================")
    print(f"          {title}           ")
    print("============================")
    print(f"SUCCESS ({len(ok_items)}):")
    for t in ok_items:
        print(f" - {t}")
    print(f"\nFAILED  ({len(fail_items)}):")
    for t, msg in fail_items:
        print(f" - {t} :: {msg}")
    print("============================\n")

def exit_code_from_failures(fail_items: Sequence[Tuple[Path, str]]) -> int:
    return 1 if fail_items else 0
