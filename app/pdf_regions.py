"""Reconstruct physical lines before combining them into nonoverlapping regions."""

from __future__ import annotations

import pymupdf


def span_box(spans: list[dict]) -> pymupdf.Rect:
    rect = pymupdf.Rect(spans[0]["bbox"])
    for span in spans[1:]:
        rect |= pymupdf.Rect(span["bbox"])
    return rect


def baseline(spans: list[dict]) -> float:
    representative = max(spans, key=lambda s: len(s["text"].strip()) * s["size"])
    return representative.get("origin", (0, representative["bbox"][3]))[1]


def visual_lines(lines: list[dict]) -> list[dict]:
    rows = []
    for line in lines:
        if tuple(line.get("dir", (1.0, 0.0))) != (1.0, 0.0):
            continue
        chunks = []
        for span in sorted(line["spans"], key=lambda s: s["bbox"][0]):
            if (
                chunks
                and span["bbox"][0] - chunks[-1][-1]["bbox"][2]
                <= max(span["size"], chunks[-1][-1]["size"]) * 2.5
            ):
                chunks[-1].append(span)
            else:
                chunks.append([span])
        for spans in chunks:
            box = span_box(spans)
            size = max(span["size"] for span in spans)
            candidate = next(
                (
                    r
                    for r in rows
                    if abs(baseline(r["spans"]) - baseline(spans)) <= size * 0.65
                    and max(box.x0 - r["bbox"].x1, r["bbox"].x0 - box.x1) <= size * 2.5
                ),
                None,
            )
            if candidate is None:
                rows.append({"bbox": box, "spans": spans})
            else:
                candidate["spans"].extend(spans)
                candidate["bbox"] |= box
    rows.sort(key=lambda row: (baseline(row["spans"]), row["bbox"].x0))
    for row in rows:
        spans = sorted(row["spans"], key=lambda s: s["bbox"][0])
        spaced = []
        for span in spans:
            if spaced:
                previous = spaced[-1]
                gap = span["bbox"][0] - previous["bbox"][2]
                if gap > max(1, span["size"] * 0.15) and (
                    not previous["text"].endswith(" ")
                    and not span["text"].startswith(" ")
                ):
                    spaced.append(
                        {
                            **span,
                            "text": " ",
                            "bbox": (
                                previous["bbox"][2],
                                span["bbox"][1],
                                span["bbox"][0],
                                span["bbox"][3],
                            ),
                        }
                    )
            spaced.append(span)
        row["spans"] = spaced
    return rows


def text_regions(data: dict):
    regions = []
    for block in data["blocks"]:
        if block.get("type") != 0:
            continue
        columns = []
        for row in visual_lines(block["lines"]):
            box = row["bbox"]
            size = max(span["size"] for span in row["spans"])
            candidates = []
            for region in columns:
                last = region["lines"][-1]
                previous = last["bbox"]
                overlap = max(0, min(previous.x1, box.x1) - max(previous.x0, box.x0))
                aligned = (
                    abs(previous.x0 - box.x0) <= size * 1.5
                    or overlap >= min(previous.width, box.width) * 0.6
                )
                if (
                    aligned
                    and baseline(row["spans"]) > baseline(last["spans"])
                    and -size * 0.5 <= box.y0 - previous.y1 <= size * 0.65
                ):
                    candidates.append(region)
            if candidates:
                region = min(
                    candidates, key=lambda r: abs(box.y0 - r["lines"][-1]["bbox"].y1)
                )
                region["lines"].append(row)
                region["bbox"] |= box
            else:
                columns.append({"bbox": pymupdf.Rect(box), "lines": [row]})
        regions.extend(columns)

    # Fonts and superscripts can cause the extractor to emit overlapping blocks.
    # They cannot be fitted independently: rebuild the shared physical region.
    combined = []
    for region in regions:
        pending = dict(region)
        pending["bbox"] = pymupdf.Rect(region["bbox"])
        pending["lines"] = list(region["lines"])
        while True:
            touching = [
                r for r in combined if (r["bbox"] & pending["bbox"]).get_area() > 0.5
            ]
            if not touching:
                break
            for other in touching:
                pending["bbox"] |= other["bbox"]
                pending["lines"].extend(other["lines"])
                combined.remove(other)
        pending["lines"] = visual_lines(pending["lines"])
        combined.append(pending)
    yield from sorted(combined, key=lambda r: (r["bbox"].y0, r["bbox"].x0))
