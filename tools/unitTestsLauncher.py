# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import sys
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import getpass
from typing import Optional

from common_utils import (
    info, warn, error, fatal,
    require_python, require_command, require_dir,
    require_docker_running,
    run_cmd, docker_mount_path,
    preflight_check
)

UNIT_TEST_PREFIX = "TEST_"

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent

UNIT_EXECUTION_FOLDER = PROJECT_ROOT / "utExecutionAndResults" / "utUnderTest"
UNIT_EXECUTION_FOLDER_BUILD = UNIT_EXECUTION_FOLDER / "build"
UNIT_RESULT_FOLDER = PROJECT_ROOT / "utExecutionAndResults" / "utResults"
RESULT_REPORT = "total_result_report.txt"

DOCKER_MOUNT = docker_mount_path(PROJECT_ROOT)

DOCKER_BASE = [
    "docker",
    "run",
    "-it",
    "--rm",
    "-v",
    f"{DOCKER_MOUNT}:/home/dev/project",
    "throwtheswitch/madsciencelab-plugins:1.0.1b",
]

CEEDLING_CLEAN = ["ceedling", "clobber"]
DOCKER_CLEAN = DOCKER_BASE + CEEDLING_CLEAN


def preflight_checks(project_root: Path):
    info("Performing preflight checks...")
    require_python(3, 8)
    require_command("docker")
    require_dir(project_root, "Project root")
    require_dir(project_root / "code", "Code directory ('code')")
    require_docker_running()
    info("Preflight checks OK.")


@dataclass
class TestResultRow:
    function_name: str
    total: str
    passed: str
    failed: str
    ignored: str
    date_time: str
    tester: str

    def to_csv_line(self) -> str:
        return (
            f"{self.function_name},"
            f"{self.total},"
            f"{self.passed},"
            f"{self.failed},"
            f"{self.ignored},"
            f"{self.date_time},"
            f"{self.tester}"
        )

@dataclass
class UnitModule:
    module_name: str
    function_name: str
    source_dir: Path
    test_root: Path

    @property
    def test_case_folder(self) -> Path:
        return self.test_root / f"{UNIT_TEST_PREFIX}{self.function_name}"

    @property
    def test_c_path(self) -> Path:
        return self.test_case_folder / "src" / f"{self.function_name}.c"


def find_function_definition(root: Path, func_name: str):
    results = []
    pattern = re.compile(rf"\b{func_name}\s*\([^)]*\)", re.IGNORECASE)

    for c_file in root.rglob("*.c"):
        try:
            with c_file.open("r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, start=1):
                    if pattern.search(line):
                        results.append((c_file, i, line.strip()))
        except Exception as e:
            warn(f"Error reading '{c_file}': {e}")
    return results


def build_modules(root: Path):
    modules = []

    for test_dir in root.rglob("*"):
        if not test_dir.is_dir():
            continue
        if not test_dir.name.startswith(UNIT_TEST_PREFIX):
            continue

        func_name = test_dir.name.replace(UNIT_TEST_PREFIX, "", 1)
        test_root = test_dir.parent
        search_root = test_root.parent

        matches = find_function_definition(search_root, func_name)

        if matches:
            file_path, _, _ = matches[0]
            module_name = file_path.name
            source_dir = file_path.parent
        else:
            warn(f"No source file found for test folder '{test_dir}'")
            continue

        modules.append(UnitModule(module_name, func_name, source_dir, test_root))

    return modules


def find_and_extract_function(file_name: str, function_name: str, unit_path: Path):
    file_path: Path | None = None

    for candidate in unit_path.rglob(file_name):
        if candidate.is_file():
            file_path = candidate
            break

    if file_path is None:
        error(f"File '{file_name}' not found in directory '{unit_path}'.")
        return None

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")

        function_pattern = re.compile(
            rf"""
            (?P<header>
                ^[ \t]*
                (?P<before>[^\n]*?)
                \b{function_name}\b
                \s*
                (?P<params>\([^)]*\))
                (?P<post_attr>
                    (?:\s*__attribute__\s*\(\([^)]*\)\))*
                )
            )
            \s*\{{
            """,
            re.MULTILINE | re.VERBOSE
        )

        match = function_pattern.search(content)
        if not match:
            warn(f"Function '{function_name}' not found in '{file_name}'.")
            return None

        brace_index = content.find("{", match.end("header"))
        if brace_index == -1:
            warn(f"Opening brace for function '{function_name}' not found in '{file_name}'.")
            return None

        open_braces = 0
        end_index = None
        for i in range(brace_index, len(content)):
            char = content[i]
            if char == "{":
                open_braces += 1
            elif char == "}":
                open_braces -= 1
                if open_braces == 0:
                    end_index = i
                    break

        if end_index is None:
            warn(f"Closing brace for function '{function_name}' not found in '{file_name}'.")
            return None

        before = match.group("before") or ""
        params = match.group("params")

        before_clean = re.sub(r'__attribute__\s*\(\([^)]*\)\)\s*', ' ', before)
        before_clean = re.sub(
            r'\b(static|inline|INLINE|extern|constexpr|volatile|register|__inline__|__forceinline)\b',
            ' ',
            before_clean
        )
        before_clean = before_clean.replace('\t', ' ')
        return_type = ' '.join(before_clean.split()) or "void"

        clean_header = f"{return_type} {function_name}{params}"
        body_part = content[brace_index:end_index + 1]
        return f"\n\n{clean_header} {body_part}"

    except Exception as e:
        error(f"Error reading file '{file_name}': {e}")
        return None


def modify_file_after_marker(file_path: Path, new_content: str):
    marker = "/* FUNCTION TO TEST */"
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        marker_index = content.find(marker)
        if marker_index == -1:
            fatal(f"Marker '{marker}' not found in file: {file_path}")

        modified_content = content[:marker_index + len(marker)] + "\n" + new_content + "\n"
        file_path.write_text(modified_content, encoding="utf-8")
        info(f"Updated file: {file_path}")
    except FileNotFoundError:
        fatal(f"File not found: {file_path}")
    except Exception as e:
        fatal(f"Error modifying file '{file_path}': {e}")


def extract_function_name(path_str: str) -> str:
    filename = Path(path_str).name
    name_no_ext = Path(filename).stem
    if name_no_ext.startswith(UNIT_TEST_PREFIX):
        return name_no_ext[len(UNIT_TEST_PREFIX):]
    return name_no_ext


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


def clear_folder(folder_path: Path):
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


def update_unit_under_test(module: UnitModule, unit_name: str):
    extracted_body = find_and_extract_function(module.module_name, module.function_name, module.source_dir)
    if extracted_body is None:
        fatal(f"Cannot extract body for function '{module.function_name}' in module '{module.module_name}'")

    modify_file_after_marker(module.test_c_path, extracted_body)
    clear_folder(UNIT_EXECUTION_FOLDER)
    copy_folder_contents(module.test_case_folder, UNIT_EXECUTION_FOLDER)


def load_result_rows(summary_file: Path) -> dict[str, TestResultRow]:
    rows: dict[str, TestResultRow] = {}
    if not summary_file.exists() or summary_file.stat().st_size == 0:
        return rows

    text = summary_file.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines:
        return rows

    first_non_empty = next((ln for ln in lines if ln.strip()), "")
    header_csv = "function_name,total,passed,failed,ignored,Date and time,Tester"

    if first_non_empty.startswith("|"):
        header_cells: list[str] = []
        data_rows_cells: list[list[str]] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|-"):
                continue
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not header_cells:
                header_cells = cells
            else:
                data_rows_cells.append(cells)

        def idx(col_name: str, default: int = -1) -> int:
            try:
                return header_cells.index(col_name)
            except ValueError:
                return default

        idx_name   = idx("function_name", 0)
        idx_total  = idx("total")
        idx_passed = idx("passed")
        idx_failed = idx("failed")
        idx_ignored = idx("ignored")
        idx_date   = idx("Date and time")
        idx_tester = idx("Tester")

        def get_cell(row: list[str], i: int) -> str:
            if i is None or i < 0 or i >= len(row):
                return ""
            return row[i]

        for row in data_rows_cells:
            fn = get_cell(row, idx_name)
            if not fn:
                continue
            rows[fn] = TestResultRow(
                function_name=fn,
                total=get_cell(row, idx_total),
                passed=get_cell(row, idx_passed),
                failed=get_cell(row, idx_failed),
                ignored=get_cell(row, idx_ignored),
                date_time=get_cell(row, idx_date),
                tester=get_cell(row, idx_tester),
            )
        return rows

    header_line = lines[0].strip()
    if "function_name" not in header_line:
        header_line = header_csv

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 5:
            continue
        while len(parts) < 7:
            parts.append("")
        fn, t, p, f, ig, dt, tst = parts[:7]
        if not fn:
            continue
        rows[fn] = TestResultRow(fn, t, p, f, ig, dt, tst)

    return rows


def update_total_result_report(build_folder: Path, function_name: str, report_folder: Path):
    pass_file = build_folder / "test" / "results" / f"test_{function_name}.pass"
    fail_file = build_folder / "test" / "results" / f"test_{function_name}.fail"
    report_file = pass_file if pass_file.exists() else fail_file

    now_str = datetime.now().strftime("%d/%m/%y %H:%M")
    tester = getpass.getuser()

    report_folder.mkdir(parents=True, exist_ok=True)
    summary_file = report_folder / RESULT_REPORT

    rows = load_result_rows(summary_file)

    # ============================================================
    # ✅ NEW BEHAVIOR: if no report exists, still update summary
    # ============================================================
    if not report_file.exists():
        warn(f"Report file does not exist: {report_file}. Marking as FAILED in summary.")

        rows[function_name] = TestResultRow(
            function_name=function_name,
            total="FAILED",
            passed="FAILED",
            failed="FAILED",
            ignored="FAILED",
            date_time=now_str,
            tester=tester
        )

        header_csv = "function_name,total,passed,failed,ignored,Date and time,Tester"
        lines_out = [header_csv] + [row.to_csv_line() for row in rows.values()]
        summary_file.write_text("\n".join(lines_out) + "\n", encoding="utf-8")

        info(f"Updated summary for '{function_name}' (FAILED - no report found): {summary_file}")
        return

    # ============================================================
    # Normal flow: report exists -> parse values as before
    # ============================================================
    total = passed = failed = ignored = None

    try:
        for line in report_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(":total:"):
                total = line.split(":", 2)[2].strip()
            elif line.startswith(":passed:"):
                passed = line.split(":", 2)[2].strip()
            elif line.startswith(":failed:"):
                failed = line.split(":", 2)[2].strip()
            elif line.startswith(":ignored:"):
                ignored = line.split(":", 2)[2].strip()

        if None in (total, passed, failed, ignored):
            warn(f"Missing values in report file: {report_file}. Marking as FAILED in summary.")
            total = passed = failed = ignored = "FAILED"

        rows[function_name] = TestResultRow(
            function_name=function_name,
            total=total,
            passed=passed,
            failed=failed,
            ignored=ignored,
            date_time=now_str,
            tester=tester
        )

        header_csv = "function_name,total,passed,failed,ignored,Date and time,Tester"
        lines_out = [header_csv] + [row.to_csv_line() for row in rows.values()]
        summary_file.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        info(f"Updated summary for '{function_name}': {summary_file}")

    except Exception as e:
        warn(f"Error updating report data: {e}. Marking as FAILED in summary.")

        rows[function_name] = TestResultRow(
            function_name=function_name,
            total="FAILED",
            passed="FAILED",
            failed="FAILED",
            ignored="FAILED",
            date_time=now_str,
            tester=tester
        )

        header_csv = "function_name,total,passed,failed,ignored,Date and time,Tester"
        lines_out = [header_csv] + [row.to_csv_line() for row in rows.values()]
        summary_file.write_text("\n".join(lines_out) + "\n", encoding="utf-8")

        info(f"Updated summary for '{function_name}' (FAILED due to exception): {summary_file}")



def format_total_result_report(report_folder: Path):
    summary_file = report_folder / RESULT_REPORT
    if not summary_file.exists():
        warn(f"Summary file does not exist: {summary_file}")
        return

    lines = summary_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        warn(f"Summary file is empty: {summary_file}")
        return

    header_parts = [h.strip() for h in lines[0].split(",") if h.strip()]
    if not header_parts:
        warn(f"Invalid header in summary file: {summary_file}")
        return

    data_rows: list[list[str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(header_parts):
            parts.extend([""] * (len(header_parts) - len(parts)))
        data_rows.append(parts[:len(header_parts)])

    if not data_rows:
        warn(f"No data rows to format: {summary_file}")
        return

    col_widths: list[int] = []
    for col_idx, header in enumerate(header_parts):
        max_data_len = max(len(row[col_idx]) for row in data_rows)
        col_widths.append(max(len(header), max_data_len))

    header_line = "| " + " | ".join(header_parts[i].ljust(col_widths[i]) for i in range(len(header_parts))) + " |\n"
    separator_line = "|" + "|".join("-" * (col_widths[i] + 2) for i in range(len(col_widths))) + "|\n"

    row_lines = ""
    for row in data_rows:
        row_lines += "| " + " | ".join(row[i].ljust(col_widths[i]) for i in range(len(row))) + " |\n"

    summary_file.write_text(header_line + separator_line + row_lines, encoding="utf-8")
    info(f"Formatted summary report: {summary_file}")


def run_and_collect_results(module: UnitModule):
    function_name = module.function_name
    update_unit_under_test(module, function_name)
    run_cmd(DOCKER_CLEAN, check=True)
    run_cmd(DOCKER_BASE + ["ceedling", f"gcov:{function_name}"], check=True, stopScript=False)
    update_total_result_report(UNIT_EXECUTION_FOLDER_BUILD, function_name, UNIT_RESULT_FOLDER)
    copy_folder_contents(UNIT_EXECUTION_FOLDER_BUILD, UNIT_RESULT_FOLDER / f"{function_name}Results")


def print_help():
    script_name = Path(sys.argv[0]).name
    print(f"""
Usage:
  python {script_name} <function_name|all>
  python {script_name} -h | --help | help
""".strip())


if __name__ == "__main__":
    preflight_check(
        script_dir=PROJECT_ROOT,
        min_python=(3,8),
        require_docker=True,
        check_docker_daemon=True,
        required_dirs=[(PROJECT_ROOT, 'Project root'), (PROJECT_ROOT / 'code', "Code directory ('code')")],
    )

    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    if sys.argv[1] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0)

    unit_to_test = extract_function_name(sys.argv[1])
    info(f"Selected argument (function to test): {unit_to_test}")

    modules = build_modules(PROJECT_ROOT)

    if unit_to_test == "all":
        clear_folder(UNIT_RESULT_FOLDER)
        for module in modules:
            info(f"Processing unit: {module.function_name}")
            try:
                run_and_collect_results(module)
            except subprocess.CalledProcessError:
                fatal(f"Unit test failed for '{module.function_name}'. See error details above.")
    else:
        unit_metadata = [m for m in modules if m.function_name == unit_to_test]
        if not unit_metadata:
            fatal(f"No module found for function '{unit_to_test}'")

        try:
            run_and_collect_results(unit_metadata[0])
        except subprocess.CalledProcessError:
            fatal(f"Unit test failed for '{unit_to_test}'. See error details above.")

    format_total_result_report(UNIT_RESULT_FOLDER)
    clear_folder(UNIT_EXECUTION_FOLDER)
    info("Done.")
