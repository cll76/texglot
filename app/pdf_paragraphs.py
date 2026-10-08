"""Reading order and paragraph requests, independent of PDF drawing regions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pdf_translation import PDFBlock

TOKEN = re.compile(r"⟪P(\d{4,})⟫")
GROUP_TOKEN = re.compile(r"⟪B(\d+)P(\d{4,})⟫")
CAPTION = re.compile(r"^(?:Figure|Fig\.|Table|Algorithm)\s*\d|^\[\d+\]", re.I)


def body_text(block: PDFBlock) -> bool:
    text = re.sub(r"\{\{math\.\d+\}\}", "", block.source).strip()
    words = re.findall(r"[A-Za-z]{2,}|[\u3400-\u9fff]", text)
    return (
        len(words) >= 4
        and len(text) >= 30
        and not text.isupper()
        and not CAPTION.match(text)
        and not (block.bold and len(text) < 160)
        and block.rect[2] - block.rect[0] >= block.fontsize * 10
    )


def reading_order(
    blocks: list[PDFBlock], sizes: list[tuple[float, float]]
) -> list[int]:
    ordered = []
    for page_number, (width, _) in enumerate(sizes):
        indices = [i for i, block in enumerate(blocks) if block.page == page_number]
        prose = [blocks[i] for i in indices if body_text(blocks[i])]
        left = [
            b
            for b in prose
            if b.rect[2] - b.rect[0] < width * 0.6 and b.rect[0] < width * 0.4
        ]
        right = [
            b
            for b in prose
            if b.rect[2] - b.rect[0] < width * 0.6 and b.rect[0] > width * 0.45
        ]
        if left and right:
            # Full-width titles/figures divide the page into reading bands.
            wide = sorted(
                [
                    i
                    for i in indices
                    if blocks[i].rect[2] - blocks[i].rect[0] >= width * 0.6
                ],
                key=lambda i: blocks[i].rect[1],
            )
            pending = set(indices) - set(wide)
            for divider in wide + [None]:
                cutoff = (
                    blocks[divider].rect[1] if divider is not None else float("inf")
                )
                band = [i for i in pending if blocks[i].rect[1] < cutoff]
                band.sort(
                    key=lambda i: (
                        blocks[i].rect[0] >= width * 0.45,
                        blocks[i].rect[1],
                        blocks[i].rect[0],
                    )
                )
                ordered.extend(band)
                pending.difference_update(band)
                if divider is not None:
                    ordered.append(divider)
        else:
            ordered.extend(
                sorted(indices, key=lambda i: (blocks[i].rect[1], blocks[i].rect[0]))
            )
    return ordered


def continues(
    previous: PDFBlock, following: PDFBlock, sizes: list[tuple[float, float]]
) -> bool:
    if (
        not body_text(previous)
        or CAPTION.match(following.source)
        or following.source.isupper()
        or following.bold
    ):
        return False
    if (
        abs(previous.fontsize - following.fontsize)
        > max(previous.fontsize, following.fontsize) * 0.15
    ):
        return False
    last = (previous.original_text or previous.source).rstrip()
    first = following.source.lstrip()
    if re.search(r"[.!?。！？][\"'’”\])}]*$", last) or (
        re.search(r"[:：][\"'’”\])}]*$", last) and not following.preceding_equation
    ):
        return False
    if not re.search(r"[A-Za-z]{2,}|[\u3400-\u9fff]|\{\{math\.", first):
        return False
    left, right = previous.rect, following.rect
    tolerance = previous.fontsize * 2
    if previous.page == following.page:
        if abs(left[0] - right[0]) <= tolerance:
            gap = right[1] - left[3]
            return -2 <= gap <= tolerance or (
                following.preceding_equation
                and 0 <= gap <= sizes[previous.page][1] * 0.25
            )
        width, height = sizes[previous.page]
        return (
            left[0] < width * 0.4
            and right[0] > width * 0.45
            and left[3] > height * 0.7
            and right[1] < height * 0.3
        )
    if following.page == previous.page + 1:
        height = sizes[previous.page][1]
        next_height = sizes[following.page][1]
        width = sizes[previous.page][0]
        next_width = sizes[following.page][0]
        return (
            left[3] > height * 0.65
            and right[1] < next_height * 0.7
            and (
                abs(left[0] / width - right[0] / next_width) < 0.08
                or (
                    left[0] > width * 0.45
                    and right[0] < next_width * 0.4
                    and left[2] - left[0] < width * 0.6
                    and right[2] - right[0] < next_width * 0.6
                )
            )
        )
    return False


@dataclass
class PDFParagraph:
    parts: list[tuple[int, PDFBlock]]

    def masked_parts(self) -> dict[str, str]:
        return {
            str(i): TOKEN.sub(lambda m: f"⟪B{i}P{m[1]}⟫", block.masked)
            for i, block in self.parts
        }

    def paragraph(self) -> str:
        texts = self.masked_parts()
        result = texts[str(self.parts[0][0])]
        for index, block in self.parts[1:]:
            following = texts[str(index)]
            if block.preceding_equation:
                result += (
                    " [original equation, read only: "
                    + block.preceding_equation
                    + "] "
                    + following
                )
                continue
            if re.search(r"[A-Za-z]-$", result) and re.match(r"[a-z]", following):
                result = result[:-1] + following
            else:
                result += " " + following
        return result

    def cache_key(self, index: int, block: PDFBlock) -> str:
        if len(self.parts) == 1:
            return block.key
        # Invalidate only text whose paragraph context changed, while avoiding
        # another model call for unchanged standalone regions on resume.
        return hashlib.sha256(
            json.dumps(
                [
                    "pdf-paragraph-v2",
                    block.key,
                    [(part.key, part.preceding_equation) for _, part in self.parts],
                ],
            ).encode()
        ).hexdigest()

    def local_tokens(self, index: int, text: str) -> str:
        if any(int(match[1]) != index for match in GROUP_TOKEN.finditer(text)):
            raise ValueError("公式或数字标记跨越了 PDF 文本区域")
        return GROUP_TOKEN.sub(lambda m: f"⟪P{int(m[2]):04d}⟫", text)


def paragraphs(
    blocks: list[PDFBlock], sizes: list[tuple[float, float]]
) -> list[PDFParagraph]:
    order = reading_order(blocks, sizes)
    margin_pages = {}
    for block in blocks:
        height = sizes[block.page][1]
        if len(block.source) < 160 and (
            block.rect[1] < height * 0.08 or block.rect[1] > height * 0.92
        ):
            text = re.sub(r"\d+", "#", block.source)
            margin_pages.setdefault(text, set()).add(block.page)
    margins = {
        i
        for i, block in enumerate(blocks)
        if len(margin_pages.get(re.sub(r"\d+", "#", block.source), ())) > 1
        and (
            block.rect[1] < sizes[block.page][1] * 0.08
            or block.rect[1] > sizes[block.page][1] * 0.92
        )
    }
    units = []
    previous_body = None
    for index in order:
        block = blocks[index]
        if index in margins or CAPTION.match(block.source):
            units.append(PDFParagraph([(index, block)]))
            continue
        if (
            previous_body
            and continues(previous_body.parts[-1][1], block, sizes)
            and sum(len(part.source) for _, part in previous_body.parts)
            + len(block.source)
            <= 6000
        ):
            previous_body.parts.append((index, block))
        else:
            unit = PDFParagraph([(index, block)])
            units.append(unit)
            previous_body = unit if body_text(block) else None
    return units
