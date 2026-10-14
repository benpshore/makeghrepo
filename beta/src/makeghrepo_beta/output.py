"""Plain output suitable for narrow SSH terminals and pipes."""

import shutil
import textwrap


def width() -> int:
    return max(32, min(88, shutil.get_terminal_size(fallback=(80, 24)).columns))


def wrap(text: str) -> str:
    return "\n".join(
        textwrap.fill(line, width=width(), break_long_words=False, break_on_hyphens=False)
        if line and not line.startswith(("/", "https://")) else line
        for line in text.splitlines()
    )


def describe_report(report: dict) -> str:
    lines = [f"Plan: {report['operation']}"]
    if "inputs" in report:
        inputs = report["inputs"]
        lines += [f"Project: {inputs['name']}", f"Owner: {inputs['owner'] or 'unverified'}"]
    for key, label in (("destination", "Destination"), ("repository_url", "Repository")):
        if report.get(key):
            lines += [f"{label}:", report[key]]
    for item in report.get("observations", []):
        lines.append(f"{item['capability']}: {item['status']}. {item['detail']}")
    lines.extend(report.get("limits", []))
    return wrap("\n".join(lines))
