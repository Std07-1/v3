"""One-shot script to fix MD032/MD031/MD009/MD022/MD058 in markdown files."""

import re
import sys


def fix_md_blanks(path: str) -> None:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    result: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        prev = result[-1].rstrip() if result else ""

        is_list = bool(
            stripped.startswith("- ")
            or stripped.startswith("* ")
            or stripped.startswith("- [")
            or re.match(r"^\d+\.\s", stripped)
        )
        prev_is_list = bool(
            prev.startswith("- ")
            or prev.startswith("* ")
            or prev.startswith("- [")
            or re.match(r"^\d+\.\s", prev)
        )
        prev_is_blank = prev == ""
        is_fence = stripped.startswith("```") or stripped.startswith("  ```")
        is_table = stripped.startswith("|")
        prev_is_table = prev.startswith("|")
        is_heading = stripped.startswith("#")
        prev_is_heading = prev.startswith("#")

        # MD022: blank line before heading if prev is non-blank
        if is_heading and not prev_is_blank and i > 0:
            result.append("\n")

        # MD032: blank line before list item if prev is non-blank, non-list
        if is_list and not prev_is_list and not prev_is_blank:
            result.append("\n")

        # MD031: blank line before fenced code block
        if is_fence and not prev_is_blank and prev != "":
            result.append("\n")

        # MD058: blank line before table if prev is non-blank, non-table
        if is_table and not prev_is_table and not prev_is_blank:
            result.append("\n")

        # MD022: blank line after heading (look-ahead in prev)
        if not stripped.startswith("#") and stripped != "" and prev_is_heading:
            result.append("\n")

        result.append(line)

    # Second pass: blank line after closing fences
    final: list[str] = []
    in_fence = False
    for i, line in enumerate(result):
        stripped = line.rstrip()
        if stripped.startswith("```") or stripped.startswith("  ```"):
            if in_fence:
                in_fence = False
                final.append(stripped + "\n")
                if i + 1 < len(result):
                    next_line = result[i + 1].rstrip()
                    if next_line != "" and not next_line.startswith("```"):
                        final.append("\n")
                continue
            else:
                in_fence = True

        # MD009: strip trailing spaces
        final.append(stripped + "\n")

    # Third pass: remove consecutive blank lines (MD012)
    cleaned: list[str] = []
    for line in final:
        if line.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(line)

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(cleaned)
    print(f"Fixed: {path}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        fix_md_blanks(p)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        fix_md_blanks(p)
