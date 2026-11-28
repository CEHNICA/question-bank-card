"""Explicit public skill resources shared by the frozen bundle and release ZIP."""

from pathlib import Path

EXTRA_RESOURCES = ("agents/openai.yaml", "assets/logo.png", "scripts/book_structure_snapshot.py")


def skill_files(root: Path) -> list[Path]:
    files = {path for path in root.rglob("*.md") if path.is_file()}
    for name in EXTRA_RESOURCES:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing maintained skill resource: {name}")
        files.add(path)
    return sorted(files)
