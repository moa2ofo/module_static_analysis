#!/usr/bin/env python3

import subprocess
import sys
import time
import platform
import yaml
import os


# ------------------------------------------------------------
# Load YAML configuration
# ------------------------------------------------------------
def load_config():
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print(f"ERROR: Configuration file '{config_path}' not found.")
        sys.exit(1)

    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------
# Help message
# ------------------------------------------------------------
def print_help():
    print(
        """
launchAll.py
============

DESCRIPTION
-----------
This script executes the complete software verification and documentation
workflow in a predefined order.

Steps and Docker settings are loaded from config.yaml.

USAGE
-----
python launchAll.py
python launchAll.py -h
python launchAll.py -help
"""
    )


# ------------------------------------------------------------
# Run external commands
# ------------------------------------------------------------
def run(cmd):
    print("\n============================================================")
    print(f"Running: {' '.join(cmd)}")
    print("============================================================\n")

    subprocess.run(cmd, check=True)


# ------------------------------------------------------------
# Ensure Docker is running (Windows Desktop version)
# ------------------------------------------------------------
def ensure_docker_running(docker_exe, max_wait_seconds):
    print("\nChecking Docker status...")

    def docker_is_ready():
        try:
            subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5,
            )
            return True
        except Exception:
            return False

    # Quick check
    if docker_is_ready():
        print("Docker is running.")
        return

    print("Docker is not running. Attempting to start Docker Desktop...")

    # Start Docker Desktop
    if not os.path.exists(docker_exe):
        print(f"ERROR: Docker executable not found at: {docker_exe}")
        sys.exit(1)

    subprocess.Popen([docker_exe])

    print(f"Waiting for Docker Desktop to become ready (timeout {max_wait_seconds}s)...")
    start = time.monotonic()

    while time.monotonic() - start < max_wait_seconds:
        if docker_is_ready():
            print("Docker successfully started.")
            return
        time.sleep(2)

    print("ERROR: Docker did not start within the expected time.")
    sys.exit(1)


# ------------------------------------------------------------
# Main workflow
# ------------------------------------------------------------
def main():
    config = load_config()

    # Validate OS
    expected_os = config.get("os", "").lower()
    current_os = platform.system().lower()

    if expected_os not in current_os:
        print(f"ERROR: Config expects OS '{expected_os}', but running on '{current_os}'.")
        sys.exit(1)

    # Docker settings
    docker_cfg = config.get("docker", {})
    docker_exe = docker_cfg.get("executable_path")
    docker_timeout = docker_cfg.get("timeout_seconds", 90)

    # Workflow steps
    steps = config.get("workflow", {}).get("steps", [])
    if not steps:
        print("ERROR: No workflow steps defined in config.yaml.")
        sys.exit(1)

    try:
        ensure_docker_running(docker_exe, docker_timeout)

        for step in steps:
            run(["python"] + step.split())

    except subprocess.CalledProcessError as e:
        print("\nERROR: Command failed:", e)
        sys.exit(e.returncode)

    print("\nAll steps completed successfully.")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "-help"):
        print_help()
        sys.exit(0)

    main()
