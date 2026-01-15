#!/usr/bin/env python3
"""
gitPullAndScriptsLauncher.py

Behavior (driven by test_module_cfg.yaml):
1) Delete all files/folders *inside* cfg.folder_path
2) Clone repo(s) and move the cloned repo folder(s) into cfg.folder_path
   2.a) If user passes a GitHub URL on CLI -> clone that single repo
   2.b) If user does NOT pass a GitHub URL -> clone all repos in cfg.repo_urls
3) Run launchAll.py located in (cfg.folder_path / "..")
   Example: folder_path="./tools/code" -> launchAll.py must be in "./tools/"

Compatible with Python 3.9+
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Exit codes
EXIT_BAD_GITHUB_URL = 1
EXIT_BAD_CFG = 7
EXIT_FOLDER_PATH_MISSING = 8
EXIT_GIT_CLONE_FAILED = 4
EXIT_LAUNCH_SCRIPT_NOT_FOUND = 5
EXIT_LAUNCH_SCRIPT_FAILED = 6


def print_help(prog_name: str) -> None:
    print(
        f"""Usage:
  {prog_name} [github_url] [--cfg <yaml_path>] [--no-submodules] [--python <python_exe>]

If github_url is provided, that repo is cloned.
If github_url is omitted, repos are taken from 'repo_urls' in the YAML config.

Config file (YAML) must contain:
  folder_path: "<target_folder_where_repos_will_be_moved>"
  repo_urls:
    - "https://github.com/owner/repo"
    - "https://github.com/owner/repo/tree/some-branch"

Options:
  --cfg             Path to YAML config (default: ./test_module_cfg.yaml)
  --no-submodules   Do not recurse into submodules
  --python          Python executable to run launchAll.py (default: current interpreter)
  -h, --help        Show this help message

Examples:
  {prog_name} --cfg ./test_module_cfg.yaml
  {prog_name} https://github.com/moa2ofo/AiSwGenRepo/tree/testAiGeneration
  {prog_name} https://github.com/moa2ofo/AiSwGenRepo --python python
"""
    )


# ----------------------------
# YAML loading (PyYAML if available, else minimal fallback)
# ----------------------------
def load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise ValueError(f"Config YAML not found: {path}")

    # Prefer PyYAML if installed
    try:
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML root must be a mapping/dict")
        return data
    except ImportError:
        # Minimal fallback that supports:
        # folder_path: "..."
        # repo_urls:
        #   - "..."
        #   - "..."
        folder_path = None
        repo_urls: List[str] = []
        in_repo_urls = False

        def strip_quotes(s: str) -> str:
            s = s.strip()
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                return s[1:-1]
            return s

        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("folder_path:"):
                    in_repo_urls = False
                    folder_path = strip_quotes(line.split(":", 1)[1].strip())
                    continue
                if line.startswith("repo_urls:"):
                    in_repo_urls = True
                    continue
                if in_repo_urls and line.startswith("-"):
                    item = strip_quotes(line[1:].strip())
                    if item:
                        repo_urls.append(item)

        data2: Dict[str, Any] = {}
        if folder_path is not None:
            data2["folder_path"] = folder_path
        if repo_urls:
            data2["repo_urls"] = repo_urls
        return data2


# ----------------------------
# GitHub URL parsing / validation
# ----------------------------
def check_branch(branch: str) -> None:
    if branch is None or branch.strip() == "":
        raise ValueError("Missing branch/tag in URL")
    # Allow common branch/tag patterns including slashes
    if re.fullmatch(r"[0-9A-Za-z._\-/]+", branch) is None:
        raise ValueError(f"Bad branch/tag format: {branch}")


def parse_github_url(url: str) -> Tuple[str, Optional[str], str]:
    """
    Accepts:
      - https://github.com/owner/repo
      - https://github.com/owner/repo/tree/<branch>
    Returns: (repo_url, branch_or_none, repo_name)
    """
    try:
        u = urlparse(url)
    except Exception as e:
        raise ValueError(f"Bad URL: {e}") from e

    if u.scheme not in ("http", "https") or u.netloc.lower() != "github.com":
        raise ValueError("URL must be a github.com http(s) URL")

    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("URL path must be /owner/repo")

    owner, repo = parts[0], parts[1]
    repo_name = repo[:-4] if repo.endswith(".git") else repo
    repo_url = f"{u.scheme}://{u.netloc}/{owner}/{repo_name}"

    branch = None
    if len(parts) >= 4 and parts[2] == "tree":
        branch = "/".join(parts[3:])
        check_branch(branch)

    return repo_url, branch, repo_name


# ----------------------------
# Filesystem helpers
# ----------------------------
def force_rmtree(path: str) -> None:
    path = os.path.abspath(path)

    def onerror(func, p, exc_info):
        exc = exc_info[1]
        if isinstance(exc, FileNotFoundError):
            return
        try:
            if not os.path.exists(p):
                return
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except FileNotFoundError:
            return

    if os.path.exists(path):
        shutil.rmtree(path, onerror=onerror)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def clear_folder_contents(folder_path: str) -> None:
    """
    Delete all files/folders *inside* folder_path, but keep folder_path itself.
    """
    if not os.path.isdir(folder_path):
        raise ValueError(f"folder_path is not a directory: {folder_path}")

    for name in os.listdir(folder_path):
        p = os.path.join(folder_path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            force_rmtree(p)
        else:
            try:
                os.chmod(p, stat.S_IWRITE)
            except Exception:
                pass
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


# ----------------------------
# Git operations
# ----------------------------
def git_clone(repository: str, branch: Optional[str], clone_dir: str, recurse_submodules: bool = True) -> None:
    if not repository.startswith("http") or "://github.com/" not in repository:
        raise ValueError("Not a GitHub http(s) URL")

    if os.path.exists(clone_dir):
        force_rmtree(clone_dir)

    cmd = ["git", "clone"]
    if recurse_submodules:
        cmd += ["--recurse-submodules"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repository, clone_dir]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git clone failed: {e}") from e


def clone_and_move_into_folder(
    github_url: str,
    target_folder: str,
    recurse_submodules: bool,
) -> str:
    """
    Clone github_url into a temp dir, then move the cloned repo folder into target_folder.
    Returns the final moved path.
    """
    repo_url, branch, repo_name = parse_github_url(github_url)

    with tempfile.TemporaryDirectory(prefix="repo_clone_") as tmp:
        clone_path = os.path.join(tmp, repo_name)
        git_clone(repo_url, branch, clone_path, recurse_submodules=recurse_submodules)

        dest_path = os.path.join(target_folder, repo_name)
        if os.path.exists(dest_path):
            force_rmtree(dest_path)

        shutil.move(clone_path, dest_path)
        return dest_path


# ----------------------------
# launchAll.py
# ----------------------------
def run_launch_all(folder_path: str, python_exe: str) -> None:
    """
    launchAll.py is expected one level above folder_path.
    Example: folder_path="./tools/code" -> launchAll.py in "./tools"
    """
    folder_path = os.path.abspath(folder_path)
    parent_dir = os.path.abspath(os.path.join(folder_path, os.pardir))
    script_path = os.path.join(parent_dir, "launchAll.py")

    if not os.path.isfile(script_path):
        print(f"ERROR: launch script not found: {script_path}")
        sys.exit(EXIT_LAUNCH_SCRIPT_NOT_FOUND)

    cmd = [python_exe, "launchAll.py"]
    try:
        subprocess.run(cmd, check=True, cwd=parent_dir)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: launchAll.py failed with exit code {e.returncode}")
        sys.exit(EXIT_LAUNCH_SCRIPT_FAILED)


# ----------------------------
# Args
# ----------------------------
def parse_args(argv: List[str]) -> Tuple[Optional[str], str, bool, str]:
    """
    CLI:
      script.py [github_url] [--cfg <yaml_path>] [--no-submodules] [--python <python_exe>]
    """
    if not argv or argv[0] in ("-h", "--help"):
        print_help(os.path.basename(sys.argv[0]))
        sys.exit(0)

    github_url: Optional[str] = None
    cfg_path = "./test_module_cfg.yaml"
    recurse_submodules = True
    python_exe = sys.executable

    i = 0
    # If first token doesn't look like an option, treat it as github_url
    if i < len(argv) and not argv[i].startswith("-"):
        github_url = argv[i]
        i += 1

    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print_help(os.path.basename(sys.argv[0]))
            sys.exit(0)

        if a == "--cfg":
            if i + 1 >= len(argv):
                print("ERROR: --cfg requires a value")
                sys.exit(1)
            cfg_path = argv[i + 1]
            i += 2
            continue

        if a == "--no-submodules":
            recurse_submodules = False
            i += 1
            continue

        if a == "--python":
            if i + 1 >= len(argv):
                print("ERROR: --python requires a value")
                sys.exit(1)
            python_exe = argv[i + 1]
            i += 2
            continue

        print(f"ERROR: Unknown argument: {a}\n")
        print_help(os.path.basename(sys.argv[0]))
        sys.exit(1)

    return github_url, cfg_path, recurse_submodules, python_exe


def validate_cfg(cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    folder_path = cfg.get("folder_path")
    if not isinstance(folder_path, str) or not folder_path.strip():
        raise ValueError("YAML must contain non-empty 'folder_path' string")

    repo_urls = cfg.get("repo_urls", [])
    if repo_urls is None:
        repo_urls = []
    if not isinstance(repo_urls, list) or any(not isinstance(x, str) for x in repo_urls):
        raise ValueError("'repo_urls' must be a list of strings")

    return folder_path, repo_urls

def clear_folder_contents(folder_path: str) -> None:
    """
    Delete all files/folders *inside* folder_path, but keep folder_path itself.
    """
    if not os.path.isdir(folder_path):
        raise ValueError(f"folder_path is not a directory: {folder_path}")

    for name in os.listdir(folder_path):
        p = os.path.join(folder_path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            force_rmtree(p)
        else:
            try:
                os.chmod(p, stat.S_IWRITE)
            except Exception:
                pass
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


def copy_folder_contents(src_dir: str, dst_dir: str) -> None:
    """
    Copy all files/folders inside src_dir into dst_dir (not nesting src_dir itself).
    """
    if not os.path.isdir(src_dir):
        raise ValueError(f"Source directory does not exist: {src_dir}")

    ensure_dir(dst_dir)

    for name in os.listdir(src_dir):
        s = os.path.join(src_dir, name)
        d = os.path.join(dst_dir, name)

        if os.path.isdir(s) and not os.path.islink(s):
            if os.path.exists(d):
                force_rmtree(d)
            shutil.copytree(s, d)
        else:
            # For files (and symlinks treated as files)
            if os.path.exists(d):
                try:
                    os.chmod(d, stat.S_IWRITE)
                except Exception:
                    pass
                try:
                    os.remove(d)
                except Exception:
                    pass
            shutil.copy2(s, d)



def main() -> None:
    github_url, cfg_path, recurse_submodules, python_exe = parse_args(sys.argv[1:])

    try:
        cfg = load_yaml(cfg_path)
        folder_path, repo_urls = validate_cfg(cfg)
    except Exception as e:
        print(f"ERROR: bad config: {e}")
        sys.exit(EXIT_BAD_CFG)

    # Ensure folder_path exists (create it if missing)
    folder_path = os.path.abspath(folder_path)
    ensure_dir(folder_path)

    # 1) Clear contents
    try:
        clear_folder_contents(folder_path)
        print(f"Cleared contents of: {folder_path}")
    except Exception as e:
        print(f"ERROR: cannot clear folder_path: {e}")
        sys.exit(EXIT_FOLDER_PATH_MISSING)

    # 2) Clone repos
    urls_to_clone: List[str]
    if github_url:
        urls_to_clone = [github_url]
    else:
        urls_to_clone = [u for u in repo_urls if u.strip()]

    if not urls_to_clone:
        print("ERROR: No github_url provided and 'repo_urls' in YAML is empty.")
        sys.exit(EXIT_BAD_CFG)

    for u in urls_to_clone:
        try:
            moved_path = clone_and_move_into_folder(
                github_url=u,
                target_folder=folder_path,
                recurse_submodules=recurse_submodules,
            )
            print(f"Cloned and moved into: {moved_path}")
        except ValueError as e:
            print(f"ERROR: Bad GitHub URL '{u}': {e}")
            sys.exit(EXIT_BAD_GITHUB_URL)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(EXIT_GIT_CLONE_FAILED)

    # 3) Run launchAll.py (one level above folder_path)
    run_launch_all(folder_path=folder_path, python_exe=python_exe)
    print("launchAll.py executed successfully.")

    # 4) After launchAll: clear folder_path contents
    try:
        clear_folder_contents(folder_path)
        print(f"Cleared contents of folder_path after launchAll: {folder_path}")
    except Exception as e:
        print(f"ERROR: cannot clear folder_path after launchAll: {e}")
        sys.exit(EXIT_FOLDER_PATH_MISSING)

    # 5) Copy ut results into ./uintTestReports (script directory)
    folder_path_abs = os.path.abspath(folder_path)
    parent_dir = os.path.abspath(os.path.join(folder_path_abs, os.pardir))

    testRecordPath = os.path.join(parent_dir, "utExecutionAndResults", "utResults")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    uintTestReports = os.path.join(script_dir, "uintTestReports")

    # If uintTestReports exists and not empty -> delete all contents inside
    ensure_dir(uintTestReports)
    if os.listdir(uintTestReports):
        try:
            clear_folder_contents(uintTestReports)
            print(f"Cleared existing contents of uintTestReports: {uintTestReports}")
        except Exception as e:
            print(f"ERROR: cannot clear uintTestReports folder: {e}")
            sys.exit(1)

    # Copy everything from testRecordPath into uintTestReports
    try:
        copy_folder_contents(testRecordPath, uintTestReports)
        print(f"Copied contents from '{testRecordPath}' into '{uintTestReports}'")
        # 6) After copying: delete all files/folders inside testRecordPath (recursive)
        try:
            clear_folder_contents(testRecordPath)
            print(f"Cleared contents of testRecordPath after copy: {testRecordPath}")
        except Exception as e:
            print(f"ERROR: cannot clear testRecordPath folder: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: cannot copy results from '{testRecordPath}' to '{uintTestReports}': {e}")
        sys.exit(1)



if __name__ == "__main__":
    main()
