from __future__ import annotations

import csv
import io
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence, TypedDict

RichConsoleModule: Any | None
RichTableModule: Any | None
try:
    import rich.console as RichConsoleModule
    import rich.table as RichTableModule
except ImportError:  # pragma: no cover
    RichConsoleModule = None
    RichTableModule = None

if TYPE_CHECKING:
    from rich.console import Console
else:  # pragma: no cover
    Console = Any


class ClocSummaryRow(TypedDict):
    language: str
    files: int
    blank: int
    comment: int
    code: int


class ClocSummary(TypedDict):
    metadata: str
    rows: list[ClocSummaryRow]


EXCLUDED_LANGUAGES = "JSON,Markdown"
FALLBACK_LANGUAGE_BY_NAME = {
    "justfile": "Justfile",
    "Justfile": "Justfile",
    "Dockerfile": "Dockerfile",
}
FALLBACK_LANGUAGE_BY_SUFFIX = {
    ".css": "CSS",
    ".html": "HTML",
    ".js": "JavaScript",
    ".py": "Python",
    ".toml": "TOML",
    ".txt": "Text",
    ".yaml": "YAML",
    ".yml": "YAML",
}
FALLBACK_EXCLUDED_DIRS = {
    ".beads",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "notebooks",
}


def count_lines_of_code() -> int:
    if shutil.which("cloc"):
        return _count_with_cloc()

    print("cloc not found, using Python fallback.", file=sys.stderr)
    return _count_with_python()


def _count_with_cloc() -> int:
    summary_cmd = [
        "cloc",
        ".",
        "--vcs=git",
        f"--exclude-lang={EXCLUDED_LANGUAGES}",
        "--exclude-dir=.beads,notebooks",
        "--csv",
        "--quiet",
    ]
    detail_cmd = [
        "cloc",
        ".",
        "--vcs=git",
        f"--exclude-lang={EXCLUDED_LANGUAGES}",
        "--exclude-dir=.beads,notebooks",
        "--by-file",
        "--csv",
        "--quiet",
    ]

    summary_output = _run_cloc(summary_cmd)
    detail_output = _run_cloc(detail_cmd)

    summary_info = _parse_cloc_summary_csv(summary_output)
    area_stats, directory_stats = _aggregate_cloc_csv(detail_output)

    if RichConsoleModule is not None and RichTableModule is not None:
        console = RichConsoleModule.Console()
        _print_heading(console, "Overall Summary")
        if summary_info["metadata"]:
            console.print(f"[dim]{summary_info['metadata']}[/dim]")
        _print_rich_cloc_summary_table(console, summary_info["rows"])
        console.print()
        _print_rich_area_table(console, area_stats)
        console.print()
        _print_rich_directory_table(console, directory_stats)
    else:
        print("Overall Summary:")
        if summary_info["metadata"]:
            print(summary_info["metadata"])
        print(_render_cloc_summary_table(summary_info["rows"]))
        print()
        print(_render_area_table(area_stats))
        print()
        print(_render_directory_table(directory_stats))

    return 0


def _count_with_python() -> int:
    language_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )
    area_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )
    directory_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )

    root = Path.cwd()
    for path in _iter_fallback_files(root):
        language = _fallback_language_for_path(path)
        if language is None:
            continue

        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                line_count = sum(1 for _ in handle)
        except OSError as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
            continue

        relative_path = path.relative_to(root)
        area = area_for_path(relative_path)
        bucket = directory_bucket_for_path(relative_path)
        language_stats[language]["files"] += 1
        language_stats[language]["lines"] += line_count
        area_stats[area]["files"] += 1
        area_stats[area]["lines"] += line_count
        directory_stats[bucket]["files"] += 1
        directory_stats[bucket]["lines"] += line_count

    if RichConsoleModule is not None and RichTableModule is not None:
        console = RichConsoleModule.Console()
        _print_heading(console, "Overall Summary")
        _print_rich_language_summary_table(console, dict(language_stats))
        console.print()
        _print_rich_area_table(console, dict(area_stats))
        console.print()
        _print_rich_directory_table(console, dict(directory_stats))
    else:
        print("Overall Summary:")
        print(_render_language_summary_table(dict(language_stats)))
        print()
        print(_render_area_table(dict(area_stats)))
        print()
        print(_render_directory_table(dict(directory_stats)))

    return 0


def _run_cloc(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        if stderr:
            print(stderr, file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
    return result.stdout


def _aggregate_cloc_csv(
    csv_output: str,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    area_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )
    directory_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "lines": 0}
    )
    reader = csv.DictReader(io.StringIO(csv_output))

    for row in reader:
        file_path = row.get("filename", "").strip()
        if not file_path or file_path == "SUM":
            continue

        try:
            line_count = int(row["code"])
        except (KeyError, TypeError, ValueError):
            continue

        area = area_for_path(file_path)
        bucket = directory_bucket_for_path(file_path)
        area_stats[area]["files"] += 1
        area_stats[area]["lines"] += line_count
        directory_stats[bucket]["files"] += 1
        directory_stats[bucket]["lines"] += line_count

    return dict(area_stats), dict(directory_stats)


def _parse_cloc_summary_csv(csv_output: str) -> ClocSummary:
    reader = csv.DictReader(io.StringIO(csv_output))
    metadata = ""
    if reader.fieldnames and len(reader.fieldnames) > 5:
        metadata = reader.fieldnames[5]

    rows: list[ClocSummaryRow] = []
    for row in reader:
        language = row.get("language", "").strip()
        if not language:
            continue
        try:
            rows.append(
                {
                    "language": language,
                    "files": int(row["files"]),
                    "blank": int(row["blank"]),
                    "comment": int(row["comment"]),
                    "code": int(row["code"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    return {"metadata": metadata, "rows": rows}


def area_for_path(path: str | Path) -> str:
    path_obj = _normalized_path(path)
    parts = path_obj.parts
    if not parts:
        return "tooling"

    if _is_test_path(path_obj):
        return "tests"
    if parts[0] == "src":
        return "src"
    if parts[0] == "scripts":
        return "scripts"
    return "tooling"


def directory_bucket_for_path(path: str | Path) -> str:
    path_obj = _normalized_path(path)
    parts = path_obj.parts
    if not parts:
        return "."

    if parts[0] == "src" and len(parts) >= 2:
        return f"src/{parts[1]}"
    if parts[0] == "tests":
        return f"tests/{parts[1]}" if len(parts) >= 3 else "tests"
    if parts[0] == "scripts":
        return "scripts"
    return parts[0] if len(parts) > 1 else "."


def _normalized_path(path: str | Path) -> Path:
    path_obj = Path(path)
    parts = [part for part in path_obj.parts if part not in {".", ""}]
    return Path(*parts) if parts else Path()


def _is_test_path(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _iter_fallback_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(
            _is_excluded_dir(partial)
            for partial in _path_prefixes(path.relative_to(root))
        ):
            continue
        yield path


def _path_prefixes(path: Path) -> list[str]:
    prefixes: list[str] = []
    parts = list(path.parts)
    for index in range(1, len(parts)):
        prefixes.append("/".join(parts[:index]))
    return prefixes


def _is_excluded_dir(path_fragment: str) -> bool:
    return path_fragment in FALLBACK_EXCLUDED_DIRS


def _fallback_language_for_path(path: Path) -> str | None:
    if path.name in FALLBACK_LANGUAGE_BY_NAME:
        return FALLBACK_LANGUAGE_BY_NAME[path.name]
    return FALLBACK_LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def _print_heading(console: Console, title: str) -> None:
    console.print(f"[blue]{title}:[/blue]")


def _render_language_summary_table(language_stats: dict[str, dict[str, int]]) -> str:
    rows = []
    total_files = 0
    total_lines = 0

    for language, stats in _sort_stats(language_stats):
        rows.append((language, str(stats["files"]), str(stats["lines"])))
        total_files += stats["files"]
        total_lines += stats["lines"]

    rows.append(("SUM", str(total_files), str(total_lines)))
    return _render_table(("Language", "Files", "Lines"), rows)


def _render_cloc_summary_table(rows: list[ClocSummaryRow]) -> str:
    rendered_rows = [
        (
            str(row["language"]),
            str(row["files"]),
            str(row["blank"]),
            str(row["comment"]),
            str(row["code"]),
        )
        for row in rows
    ]
    return _render_table(
        ("Language", "Files", "Blank", "Comment", "Code"), rendered_rows
    )


def _render_area_table(area_stats: dict[str, dict[str, int]]) -> str:
    total_lines = sum(stats["lines"] for stats in area_stats.values())
    rows = []
    for area, stats in _sort_stats(area_stats):
        share = _format_share(stats["lines"], total_lines)
        rows.append((area, str(stats["files"]), str(stats["lines"]), share))
    return _render_table(
        ("Area", "Files", "Lines", "Share"), rows, title="Repository Overview"
    )


def _render_directory_table(directory_stats: dict[str, dict[str, int]]) -> str:
    rows = [
        (directory, str(stats["files"]), str(stats["lines"]))
        for directory, stats in _sort_stats(directory_stats)
    ]
    return _render_table(
        ("Directory", "Files", "Lines"), rows, title="Lines of Code by Directory"
    )


def _print_rich_language_summary_table(
    console: Console,
    language_stats: dict[str, dict[str, int]],
) -> None:
    assert RichTableModule is not None
    table = RichTableModule.Table(padding=(0, 1))
    table.add_column("Language", style="cyan", no_wrap=True)
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Lines", justify="right", style="green")

    total_files = 0
    total_lines = 0
    for language, stats in _sort_stats(language_stats):
        table.add_row(language, str(stats["files"]), str(stats["lines"]))
        total_files += stats["files"]
        total_lines += stats["lines"]

    table.add_section()
    table.add_row("SUM", str(total_files), str(total_lines))
    console.print(table)


def _print_rich_cloc_summary_table(
    console: Console,
    rows: list[ClocSummaryRow],
) -> None:
    assert RichTableModule is not None
    table = RichTableModule.Table(padding=(0, 1))
    table.add_column("Language", style="cyan", no_wrap=True)
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Blank", justify="right")
    table.add_column("Comment", justify="right")
    table.add_column("Code", justify="right", style="green")

    for row in rows:
        if row["language"] == "SUM":
            table.add_section()
        table.add_row(
            str(row["language"]),
            str(row["files"]),
            str(row["blank"]),
            str(row["comment"]),
            str(row["code"]),
        )

    console.print(table)


def _print_rich_area_table(
    console: Console,
    area_stats: dict[str, dict[str, int]],
) -> None:
    assert RichTableModule is not None
    total_lines = sum(stats["lines"] for stats in area_stats.values())
    table = RichTableModule.Table(title="Repository Overview", padding=(0, 1))
    table.add_column("Area", style="cyan", no_wrap=True)
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Lines", justify="right", style="green")
    table.add_column("Share", justify="right", style="yellow")

    for area, stats in _sort_stats(area_stats):
        table.add_row(
            area,
            str(stats["files"]),
            str(stats["lines"]),
            _format_share(stats["lines"], total_lines),
        )

    console.print(table)


def _print_rich_directory_table(
    console: Console,
    directory_stats: dict[str, dict[str, int]],
) -> None:
    assert RichTableModule is not None
    table = RichTableModule.Table(title="Lines of Code by Directory", padding=(0, 1))
    table.add_column("Directory", style="cyan", no_wrap=True)
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Lines", justify="right", style="green")

    for directory, stats in _sort_stats(directory_stats):
        table.add_row(directory, str(stats["files"]), str(stats["lines"]))

    console.print(table)


def _sort_stats(
    stats_by_key: dict[str, dict[str, int]],
) -> list[tuple[str, dict[str, int]]]:
    return sorted(
        stats_by_key.items(),
        key=lambda item: (item[1]["lines"], item[1]["files"], item[0]),
        reverse=True,
    )


def _format_share(lines: int, total_lines: int) -> str:
    if total_lines == 0:
        return "0.0%"
    return f"{(lines / total_lines) * 100:5.1f}%"


def _render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    title: str | None = None,
) -> str:
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]

    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    output = []
    if title:
        output.append(title)
    output.append(border)
    output.append(
        "| "
        + " | ".join(
            header.ljust(widths[index]) if index == 0 else header.rjust(widths[index])
            for index, header in enumerate(headers)
        )
        + " |"
    )
    output.append(border)
    for row in rows:
        output.append(
            "| "
            + " | ".join(
                value.ljust(widths[index]) if index == 0 else value.rjust(widths[index])
                for index, value in enumerate(row)
            )
            + " |"
        )
    output.append(border)
    return "\n".join(output)
