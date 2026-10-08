import unicodedata

import pymupdf

from app.pdf_regions import baseline, text_regions, visual_lines
from app.pdf_translation import extract_blocks, open_pdf, render_translation


def run_in_heading_paper(path):
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        heading = "Quality-aware efficiency."
        page.insert_text((50, 120), heading, fontname="tibo", fontsize=12)
        x = 50 + pymupdf.get_text_length(heading, fontname="tibo", fontsize=12) + 12
        page.insert_text(
            (x, 120), "Green AI examines task per-", fontname="tiro", fontsize=12
        )
        page.insert_textbox(
            pymupdf.Rect(50, 123, 520, 200),
            "formance (Schwartz et al., 2020). Model routing adapts which agents contribute to a request, and prompt compression reduces context length.",
            fontname="tiro",
            fontsize=12,
        )
        document.save(path)


async def test_run_in_heading_and_first_body_line_are_one_complete_nonoverlapping_box(
    tmp_path,
):
    source, target = tmp_path / "source.pdf", tmp_path / "translated.pdf"
    run_in_heading_paper(source)
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
    assert len(blocks) == 1
    block = blocks[0]
    assert block.source.startswith(
        "Quality-aware efficiency. Green AI examines task performance"
    )
    assert "(Schwartz et al., 2020)" in block.source
    assert len(block.text_rects) > 1
    output = "质量感知的效率。绿色人工智能考察任务性能（Schwartz 等，⟪P0000⟫）。模型路由调整参与请求的智能体，提示压缩缩短上下文。"
    assert not await render_translation(
        source, target, blocks, {block.key: output}, tmp_path / "assets"
    )
    with open_pdf(target) as document:
        text = unicodedata.normalize("NFKC", document[0].get_text())
        assert (
            "质量感知" in text
            and "Quality-aware" not in text
            and "Green AI" not in text
        )
        lines = [
            line
            for b in document[0].get_text("dict")["blocks"]
            for line in b.get("lines", [])
        ]
        rows = visual_lines(lines)
        assert len(rows) == 2
        for first, second in zip(rows, rows[1:]):
            assert (
                baseline(second["spans"]) - baseline(first["spans"])
                >= block.fontsize * 0.9
            )


def test_separate_extractor_blocks_on_same_baseline_are_rebuilt_in_reading_order():
    def line(text, box, origin):
        return {
            "dir": (1, 0),
            "spans": [
                {
                    "text": text,
                    "bbox": box,
                    "origin": origin,
                    "size": 10,
                    "font": "Times-Roman",
                    "color": 0,
                    "flags": 0,
                }
            ],
        }

    data = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    line("Our result.", (50, 90, 110, 101), (50, 98)),
                    line(
                        "Following text completes this paragraph.",
                        (50, 103, 400, 114),
                        (50, 111),
                    ),
                ],
            },
            {
                "type": 0,
                "lines": [
                    line("The experiment confirms it.", (120, 90, 400, 101), (120, 98))
                ],
            },
            {
                "type": 0,
                "lines": [
                    line("Independent next paragraph.", (50, 145, 350, 156), (50, 153))
                ],
            },
        ]
    }
    regions = list(text_regions(data))
    assert len(regions) == 2
    assert len(regions[0]["lines"]) == 2
    text = " ".join(
        "".join(s["text"] for s in line["spans"]) for line in regions[0]["lines"]
    )
    assert (
        text
        == "Our result. The experiment confirms it. Following text completes this paragraph."
    )
    assert not regions[0]["bbox"].intersects(regions[1]["bbox"])


def test_math_superscripts_do_not_split_the_baseline_or_overlap_other_regions():
    def span(text, rect, origin, size=10, font="Times-Roman"):
        return {
            "text": text,
            "bbox": rect,
            "origin": origin,
            "size": size,
            "font": font,
            "color": 0,
            "flags": 0,
        }

    data = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "dir": (1, 0),
                        "spans": [span("We use", (50, 90, 80, 102), (50, 99))],
                    },
                    {
                        "dir": (1, 0),
                        "spans": [
                            span("x", (83, 90, 90, 102), (83, 99), font="CMMI10"),
                            span("2", (90, 86, 95, 94), (90, 91), size=7, font="CMR7"),
                        ],
                    },
                    {
                        "dir": (1, 0),
                        "spans": [
                            span("in the experiment.", (99, 90, 220, 102), (99, 99))
                        ],
                    },
                    {
                        "dir": (1, 0),
                        "spans": [
                            span(
                                "The result remains reliable.",
                                (50, 104, 240, 116),
                                (50, 113),
                            )
                        ],
                    },
                ],
            }
        ]
    }
    regions = list(text_regions(data))
    assert len(regions) == 1 and len(regions[0]["lines"]) == 2
    first = "".join(s["text"] for s in regions[0]["lines"][0]["spans"])
    assert first == "We use x2 in the experiment."
