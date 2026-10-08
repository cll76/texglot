"""Locate corresponding source paragraphs in the rendered PDFs."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pymupdf

from .latex import (
    MARKER,
    collect_literal_macros,
    collect_math_aliases,
    collect_numeric_registers,
    collect_prose_arguments,
    collect_text_macros,
    collect_title_macros,
    segments,
)


def searchable(text: str) -> str:
    # PDF line wraps, discretionary hyphens and font ligatures change the
    # spelling extracted from the page without changing the rendered prose.
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text).casefold()
        if char.isalnum()
    )


class TextPositions:
    def __init__(self, path: Path):
        letters, self.positions = [], []
        with pymupdf.open(path) as document:
            for number, page in enumerate(document, 1):
                flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                for block in page.get_text("rawdict", flags=flags)["blocks"]:
                    for line in block.get("lines", []):
                        for span in line["spans"]:
                            for char in span["chars"]:
                                value = searchable(char["c"])
                                if not value:
                                    continue
                                box = pymupdf.Rect(char["bbox"]) * page.rotation_matrix
                                top = max(0, min(1, box.y0 / page.rect.height))
                                bottom = max(0, min(1, box.y1 / page.rect.height))
                                letters.append(value)
                                self.positions.extend(
                                    [(number, top, bottom)] * len(value)
                                )
        self.text = "".join(letters)

    def find(self, text: str, *, end: bool = False):
        value = searchable(text)
        if len(value) < 16:
            return None
        probe = value[-48:] if end else value[:48]
        start = self.text.find(probe)
        if start < 0 or self.text.find(probe, start + 1) >= 0:
            return None
        page, top, bottom = self.positions[start + len(probe) - 1 if end else start]
        return {"page": page, "fraction": bottom if end else top}


def paragraph_pairs(
    original: Path,
    translated: Path,
    source_files: tuple[str, ...],
    dependencies: tuple[str, ...],
):
    folder = original.parent
    source, target = folder / "prepared-source", folder / "translated"
    if not source_files or not source.is_dir() or not target.is_dir():
        return []
    context = "\n".join(
        (source / name).read_text(encoding="utf-8")
        for name in dependencies or source_files
        if (source / name).is_file()
    )
    options = {
        "text_macros": collect_text_macros(context),
        "math_aliases": collect_math_aliases(context),
        "literal_macros": collect_literal_macros(context),
        "numeric_registers": collect_numeric_registers(context),
        "prose_arguments": collect_prose_arguments(context),
        "title_macros": collect_title_macros(context),
    }
    indexes = None
    candidates = []
    for name in source_files:
        if not (source / name).is_file() or not (target / name).is_file():
            continue
        # Language-dependent character counts must not split corresponding
        # paragraphs at different places. Keep the parser's natural boundaries.
        originals, translations = (
            segments(
                (root / name).read_text(encoding="utf-8"),
                max_chars=float("inf"),
                **options,
            )
            for root in (source, target)
        )
        if len(originals) != len(translations):
            continue
        if indexes is None:
            indexes = (TextPositions(original), TextPositions(translated))
        for number, (before, after) in enumerate(zip(originals, translations)):
            if before.role != after.role or before.protected != after.protected:
                continue
            runs = list(zip(MARKER.split(before.masked), MARKER.split(after.masked)))
            for edge in ("start", "end"):
                for first, second in reversed(runs) if edge == "end" else runs:
                    positions = [
                        index.find(text, end=edge == "end")
                        for index, text in zip(indexes, (first, second))
                    ]
                    if all(positions):
                        candidates.append(
                            {
                                "id": f"paragraph:{name}:{number}:{edge}",
                                "weight": 6,
                                "original": positions[0],
                                "translated": positions[1],
                            }
                        )
                        break
    return candidates
