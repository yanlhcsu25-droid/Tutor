from __future__ import annotations

from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = [ROOT / "tests"]

LEGACY_TOOL_MAP = {
    "read_current_paper": "read_paper",
    "analyze_current_paper": "analyze_paper",
    "preview_generation_plan": "prepare_generation_plan",
    "confirm_generation_plan": "confirm_generation",
    "preview_replace_question": "preview_paper_changes",
    "preview_adjust_paper": "preview_paper_changes",
    "preview_add_question": "preview_paper_changes",
    "confirm_replace_question": "confirm_paper_changes",
    "confirm_adjust_paper": "confirm_paper_changes",
    "cancel_replace_question": "discard_pending_plan",
    "undo_paper": "operate_paper_version(action=undo)",
    "redo_paper": "operate_paper_version(action=redo)",
    "restore_paper_version": "operate_paper_version(action=restore)",
}

SKIP_PARTS = {"__pycache__"}
SKIP_NAMES = {"latest.json"}


def iter_candidate_files():
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.name in SKIP_NAMES:
                continue
            if path.suffix not in {".py", ".yaml", ".yml", ".md", ".json"}:
                continue
            yield path


def main() -> None:
    hits: dict[Path, list[str]] = defaultdict(list)

    for path in iter_candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for legacy_name in LEGACY_TOOL_MAP:
            if legacy_name in text:
                hits[path].append(legacy_name)

    if not hits:
        print("No legacy Agent Tool references found.")
        return

    print("Legacy Agent Tool references found:\n")
    for path in sorted(hits):
        rel = path.relative_to(ROOT)
        print(rel)
        for legacy_name in sorted(hits[path]):
            print(f"  - {legacy_name} -> {LEGACY_TOOL_MAP[legacy_name]}")
        print()

    print(f"Files requiring review: {len(hits)}")
    print("\nImportant: do NOT mechanically replace every occurrence.")
    print("Low-level deterministic service names may remain valid internal APIs.")
    print("Only Agent-visible contracts should migrate to the new 8-tool surface.")


if __name__ == "__main__":
    main()
