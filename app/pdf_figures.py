"""Locate placed artwork so its selectable labels remain untouched."""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache

import pymupdf
from pypdf.generic import ContentStream, DecodedStreamObject


def figure_regions(page: pymupdf.Page, text: dict) -> list[pymupdf.Rect]:
    document = page.parent
    forms = defaultdict(dict)
    for xref, name, parent, bbox in page.get_xobjects():
        forms[parent][name] = (xref, pymupdf.Rect(bbox))
    images = defaultdict(set)
    for image in page.get_images(full=True):
        images[image[-1]].add(image[7])

    @lru_cache(maxsize=None)
    def operations(xref):
        stream = DecodedStreamObject()
        stream.set_data(document.xref_stream(xref))
        return ContentStream(stream, None).operations

    @lru_cache(maxsize=None)
    def artwork(xref):
        for args, op in operations(xref):
            if op in (b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh"):
                return True
            if op == b"Do":
                name = str(args[0]).lstrip("/")
                if name in images[xref]:
                    return True
                if name in forms[xref] and artwork(forms[xref][name][0]):
                    return True
        return False

    regions = []
    matrix = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    stack = []
    stream = DecodedStreamObject()
    stream.set_data(
        b"\n".join(document.xref_stream(xref) for xref in page.get_contents())
    )
    for args, op in ContentStream(stream, None).operations:
        if op == b"q":
            stack.append(pymupdf.Matrix(matrix))
        elif op == b"Q":
            matrix = stack.pop()
        elif op == b"cm":
            matrix = pymupdf.Matrix(*map(float, args)) * matrix
        elif op == b"Do":
            name = str(args[0]).lstrip("/")
            if name in forms[0]:
                xref, bbox = forms[0][name]
                rect = bbox * matrix * page.transformation_matrix
                # Whole-page forms wrap ordinary document content, rather than
                # identifying an illustration. Text-only forms are also prose.
                if artwork(xref) and not (
                    rect.width >= page.rect.width * 0.85
                    and rect.height >= page.rect.height * 0.85
                ):
                    regions.append(rect)

    ocr = any(span["type"] == 3 for span in page.get_texttrace())
    for image in page.get_image_info():
        rect = pymupdf.Rect(image["bbox"])
        if ocr and rect.get_area() >= page.rect.get_area() * 0.8:
            continue  # A scanned page background is not an embedded figure.
        regions.append(rect)

    # Direct vector drawings have no enclosing Form XObject. Keep their labels
    # as well. Native tables identified by nearby table captions remain text.
    table_captions = []
    for block in text["blocks"]:
        lines = block.get("lines", [])
        value = "".join(
            span["text"] for line in lines for span in line["spans"]
        ).lstrip()
        if re.match(r"^(?:Table(?:\s|\d)|表\s*\d)", value, re.I):
            table_captions.append(pymupdf.Rect(block["bbox"]))
    for rect in page.cluster_drawings():
        if rect.width < 30 or rect.height < 20:
            continue
        table = any(
            caption.x0 < rect.x1
            and caption.x1 > rect.x0
            and (0 <= rect.y0 - caption.y1 < 36 or 0 <= caption.y0 - rect.y1 < 36)
            for caption in table_captions
        )
        if not table:
            regions.append(rect)
    return regions


def inside_figure(rect: pymupdf.Rect, regions: list[pymupdf.Rect]) -> bool:
    return any(
        (rect & figure).get_area() >= rect.get_area() * 0.6 for figure in regions
    )
