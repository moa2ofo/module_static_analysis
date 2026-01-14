#!/usr/bin/env python3
from __future__ import annotations

import os
import getpass
from datetime import datetime
import xml.etree.ElementTree as ET
import html
import re
from pathlib import Path
from typing import Union, Dict
import sys

from common_utils import (
    info, warn, error, fatal,
    require_python, require_command, require_file, require_dir,
    require_docker_running,
    run_cmd,
    find_targets_with_subfolders,
    preflight_check,
)

# Default MISRA rules file path (adjust if needed)
MISRA_RULES_PATH = Path(__file__).resolve().parent / "misra" / "misra_c_2012_headlines.txt"


# -------------------------
# Requirements / preflight
# -------------------------
def preflight_checks(script_dir: Path) -> None:
    """Verify all required prerequisites before starting."""
    info("Performing preflight checks...")

    require_python(3, 8)
    require_command("docker")

    require_file(script_dir / "CMakeLists.txt", "Template CMakeLists.txt")
    require_dir(script_dir / "code", "Code directory")

    if not MISRA_RULES_PATH.is_file():
        warn(
            f"MISRA rules file not found: {MISRA_RULES_PATH}. "
            "cppcheck severities will be used instead."
        )

    require_docker_running()
    info("Preflight checks OK.")


# ----------------------------------------------------
# Existing logic (unchanged, except for robust I/O)
# ----------------------------------------------------
def load_misra_rules(misra_rules_path: Union[str, Path]) -> Dict[str, str]:
    rules: Dict[str, str] = {}
    path = Path(misra_rules_path)

    if not path.is_file():
        warn(f"MISRA rules file not found: {path}. cppcheck severities will be used instead.")
        return rules

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("Rule "):
                continue

            rest = line[5:].strip()
            parts = rest.split("\t")
            if len(parts) < 2:
                parts = rest.split(None, 1)
                if len(parts) < 2:
                    continue

            rule_id = parts[0].strip()
            severity = parts[1].strip()

            if rule_id:
                rules[rule_id] = severity

    info(f"Loaded {len(rules)} MISRA rules from {path}")
    return rules


MISRA_ID_REGEX = re.compile(r"misra-c2012-(\d+\.\d+)")

def generate_html_for_cppcheck_xml(xml_path: Union[str, Path], misra_rules_path: Union[str, Path]) -> str:
    xml_path = str(xml_path)
    xml_dir = os.path.dirname(xml_path)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    errors = root.findall(".//error")
    if not errors:
        info(f"No <error> elements found in {xml_path}")
        return ""

    misra_rules = load_misra_rules(misra_rules_path)

    attr_names = set()
    for err in errors:
        attr_names.update(err.attrib.keys())

    attr_names.discard("cwe")
    attr_names.discard("file0")
    attr_names.discard("verbose")

    preferred_order = ["id", "severity", "file1", "msg"]
    ordered_attrs = [a for a in preferred_order if a in attr_names]
    ordered_attrs.extend(sorted(attr_names - set(ordered_attrs)))
    columns = ordered_attrs + ["locations"]

    rows_html = []

    for err in errors:
        cells = []

        err_id = err.attrib.get("id", "")
        severity_val = ""

        m = MISRA_ID_REGEX.search(err_id)
        if m:
            rule_number = m.group(1)
            severity_val = misra_rules.get(rule_number, "")

        if not severity_val:
            severity_val = err.attrib.get("severity", "")

        severity_for_row = severity_val or ""
        sev_norm = severity_for_row.strip().lower()

        row_style = ""
        if "advisory" in sev_norm:
            row_style = ' style="background-color: #ffff99;"'
        elif "required" in sev_norm:
            row_style = ' style="background-color: #ffcc80;"'
        elif "mandatory" in sev_norm:
            row_style = ' style="background-color: #ff9999;"'

        for col in ordered_attrs:
            val = severity_for_row if col == "severity" else err.attrib.get(col, "")
            cells.append("<td>%s</td>" % html.escape(val))

        locations_html = []
        for loc in err.findall("location"):
            file_ = loc.attrib.get("file", "")
            line = loc.attrib.get("line", "")
            col_ = loc.attrib.get("column", "")
            info_txt = loc.attrib.get("info", "")

            display_parts = []
            if file_:
                display_parts.append(file_)
            if line:
                display_parts.append(line)
            if col_:
                display_parts.append(col_)
            display_text = ":".join(display_parts)

            abs_path = os.path.abspath(os.path.join(xml_dir, file_)) if file_ else ""

            if abs_path:
                abs_path_norm = abs_path.replace("\\", "/")

                if line:
                    vscode_target = f"vscode://file/{abs_path_norm}:{line}"
                    if col_:
                        vscode_target += f":{col_}"
                else:
                    vscode_target = f"vscode://file/{abs_path_norm}"
            else:
                vscode_target = ""
            link_text = html.escape(display_text) if display_text else html.escape(file_)
            info_html = f" - {html.escape(info_txt)}" if info_txt else ""

            if abs_path:
                link_html = f'<a href="{html.escape(vscode_target)}">{link_text}</a>{info_html}'
            else:
                link_html = f"{link_text}{info_html}"

            locations_html.append(link_html)

        loc_html = "<br>".join(locations_html)
        cells.append("<td>%s</td>" % loc_html)

        rows_html.append("<tr%s>%s</tr>" % (row_style, "".join(cells)))

    header_cells = "".join("<th>%s</th>" % html.escape(col) for col in columns)
    header_html = "<tr>%s</tr>" % header_cells

    css = """
table {
    border-collapse: collapse;
    width: 100%;
    font-family: Arial, sans-serif;
    font-size: 14px;
}
th, td {
    border: 1px solid #ccc;
    padding: 4px 8px;
}
th {
    background-color: #f2f2f2;
}
tbody tr:hover td {
    background-color: #e8f2ff;
}
.meta {
    margin: 6px 0 10px 0;
}
"""

    title = "Cppcheck MISRA Results"
    tester = getpass.getuser()
    now = datetime.now()
    meta_line = f"Tester: {html.escape(tester)}&nbsp;&nbsp;Date: {now:%d/%m/%y}&nbsp;&nbsp;Time: {now:%H:%M:%S}"

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta"><strong>{meta_line}</strong></p>
<p>Source file: <code>{html.escape(xml_path)}</code></p>
<table>
<thead>{header_html}</thead>
<tbody>{''.join(rows_html)}</tbody>
</table>
</body>
</html>
"""

    html_path = os.path.splitext(xml_path)[0] + ".html"
    Path(html_path).write_text(html_doc, encoding="utf-8")

    cleaned = Path(html_path).read_text(encoding="utf-8").replace("\\011", " ").replace("\\342\\200\\246", "…")
    Path(html_path).write_text(cleaned, encoding="utf-8")

    info(f"Generated: {html_path}")

    try:
        os.remove(xml_path)
        info(f"Deleted source XML: {xml_path}")
    except Exception as e:
        warn(f"Could not delete {xml_path}: {e}")

    return html_path


def generate_cppcheck_html_reports(root_folder: Union[str, Path], misra_rules_path: Union[str, Path]) -> None:
    root_folder = str(root_folder)
    for dirpath, _, filenames in os.walk(root_folder):
        for filename in filenames:
            if filename in ("cppcheck_misra_results.mxl", "cppcheck_misra_results.xml"):
                xml_path = os.path.join(dirpath, filename)
                try:
                    generate_html_for_cppcheck_xml(xml_path, misra_rules_path)
                except Exception as e:
                    error(f"Failed to process {xml_path}: {e}")


def scan_components(codebase_root: Path, template_content: str) -> list[Path]:
    info(f"Scanning for components under: {codebase_root}")
    created: list[Path] = []

    for target_dir in find_targets_with_subfolders(codebase_root, ("pltf", "cfg")):
        cmake_path = target_dir / "CMakeLists.txt"
        if cmake_path.exists():
            info(f"Skipping existing CMakeLists.txt in: {target_dir}")
            continue

        project_name = target_dir.name
        component_content = template_content.replace("projectName", project_name)

        info(f"Creating CMakeLists.txt in: {target_dir} (projectName -> {project_name})")
        try:
            cmake_path.write_text(component_content, encoding="utf-8")
        except Exception as e:
            error(f"Failed to write {cmake_path}: {e}")
            continue

        created.append(cmake_path)

    if not created:
        warn(f"No new CMakeLists.txt files were created under {codebase_root}. Nothing to do.")
        sys.exit(0)

    info("CMakeLists.txt created in:")
    for p in created:
        print(f" - {p}")
    return created


def build_and_run_docker(script_dir: Path) -> None:
    info("Building Docker image: cmake-misra-multi")
    run_cmd(["docker", "build", "-t", "cmake-misra-multi", "."], cwd=script_dir, check=True)

    cwd = str(script_dir.resolve())
    info(f"Running analysis container with workspace: {cwd}")

    run_cmd(
        ["docker", "run", "--rm", "-v", f"{cwd}:/workspace", "cmake-misra-multi", "build-and-check-all.sh"],
        check=True,
    )


def generate_reports(codebase_root: Path, misra_rules_path: Path) -> None:
    info(f"Using MISRA rules file: {misra_rules_path}")
    generate_cppcheck_html_reports(codebase_root, misra_rules_path)


def _cleanup_generated(created: list[Path]) -> None:
    if not created:
        info("No generated CMakeLists.txt to clean up.")
        return
    info("Cleaning up generated CMakeLists.txt files...")
    for cmake_path in created:
        try:
            cmake_path.unlink()
            info(f"Removed {cmake_path}")
        except FileNotFoundError:
            warn(f"Already removed: {cmake_path}")
        except Exception as e:
            warn(f"Could not delete {cmake_path}: {e}")


def main():
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    preflight_check(
        script_dir=script_dir,
        min_python=(3,8),
        require_docker=True,
        check_docker_daemon=True,
        required_dirs=[(script_dir / 'code', 'Code directory')],
        required_files=[(script_dir / 'CMakeLists.txt', 'Template CMakeLists.txt')],
        optional_files=[(MISRA_RULES_PATH, 'MISRA rules file')],
    )

    template_path = script_dir / "CMakeLists.txt"
    codebase_root = script_dir / "code"

    template_content = template_path.read_text(encoding="utf-8", errors="replace")
    misra_rules_path = MISRA_RULES_PATH

    created: list[Path] = []
    try:
        created = scan_components(codebase_root, template_content)
        build_and_run_docker(script_dir)
    except subprocess.CalledProcessError:
        fatal("Docker build/run failed. See error details above.")
    finally:
        _cleanup_generated(created)

    generate_reports(codebase_root, misra_rules_path)
    info("Done.")


if __name__ == "__main__":
    main()