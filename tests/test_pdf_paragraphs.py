import json

import pytest

from app.config import Settings
from app.pdf_paragraphs import PDFParagraph, paragraphs, reading_order
from app.pdf_translation import PDFBlock, translate_paragraph


def block(page, text, rect=(50, 100, 550, 150), protected=None, bold=False):
    protected = protected or []
    masked = text
    for index, value in enumerate(protected):
        masked = masked.replace(value, f"⟪P{index:04d}⟫", 1)
    return PDFBlock(page, rect, text, masked, protected, {}, 12, "#000000", bold)


def crossed_page_blocks():
    return [
        block(0, "Under review as a conference paper", (50, 20, 500, 35)),
        block(
            0,
            "We observe that historical influence persists even after its positive util-",
            (50, 680, 550, 735),
        ),
        block(1, "Under review as a conference paper", (50, 20, 500, 35)),
        block(
            1,
            "ity has decayed. We confirm the result with 8 trials.",
            (50, 75, 550, 130),
            ["8"],
        ),
        block(1, "Figure 2: A summary of the results", (50, 200, 550, 240)),
        block(
            1,
            "This next paragraph is independent and discusses another experiment.",
            (50, 250, 550, 300),
        ),
    ]


def test_cross_page_paragraph_ignores_header_and_repairs_split_word():
    blocks = crossed_page_blocks()
    units = paragraphs(blocks, [(600, 800)] * 2)
    joined = [u for u in units if len(u.parts) > 1]
    assert len(joined) == 1
    assert [i for i, _ in joined[0].parts] == [1, 3]
    text = joined[0].paragraph()
    assert "positive utility has decayed" in text and "util-" not in text
    assert "Under review" not in text and "Figure" not in text
    assert "⟪B3P0000⟫" in text
    single = next(u for u in units if u.parts[0][0] == 5)
    assert single.cache_key(5, blocks[5]) == blocks[5].key
    assert joined[0].cache_key(1, blocks[1]) != blocks[1].key
    changed = PDFParagraph(
        [(1, blocks[1]), (3, block(1, "ity has decayed under different conditions."))]
    )
    assert joined[0].cache_key(1, blocks[1]) != changed.cache_key(1, blocks[1])


def test_two_column_reading_order_and_continuation_to_next_page():
    left_top = block(
        0, "We begin with the first sentence in the left column.", (40, 80, 280, 140)
    )
    left_bottom = block(
        0, "This is the end of the left column and it continues", (40, 680, 280, 735)
    )
    right_top = block(
        0, "into the right column with the same argument.", (320, 80, 560, 140)
    )
    right_bottom = block(
        0, "We then compare with prior methods and it continues", (320, 680, 560, 735)
    )
    next_page = block(
        1, "on the next page without breaking the sentence.", (40, 80, 280, 140)
    )
    next_right = block(
        1, "This is a separate paragraph on the right.", (320, 80, 560, 140)
    )
    blocks = [right_top, left_bottom, left_top, right_bottom, next_page, next_right]
    order = reading_order(blocks, [(600, 800)] * 2)
    assert order == [2, 1, 0, 3, 4, 5]
    groups = [[i for i, _ in u.parts] for u in paragraphs(blocks, [(600, 800)] * 2)]
    assert [1, 0] in groups and [3, 4] in groups


def test_headings_captions_tables_and_complete_paragraphs_do_not_merge():
    blocks = [
        block(0, "This paragraph ends with a complete sentence.", (50, 80, 550, 120)),
        block(
            0,
            "another paragraph starts a different discussion here.",
            (50, 125, 550, 160),
        ),
        block(0, "Figure 2: the caption has its own description", (50, 170, 550, 205)),
        block(0, "SUMMARY OF THE EXPERIMENTAL RESULTS", (50, 210, 550, 240)),
        block(0, "Agent", (50, 245, 90, 260)),
        block(0, "[12] An author and a reference title.", (50, 270, 550, 300)),
    ]
    assert all(len(u.parts) == 1 for u in paragraphs(blocks, [(600, 800)]))


def test_cross_page_sentence_continues_below_a_float_and_its_caption():
    blocks = [
        block(
            0, "Earlier work examines how historical information", (50, 675, 550, 735)
        ),
        block(1, "Figure 1: The overview of the method.", (50, 245, 550, 270)),
        block(
            1, "Can affect the next task even after retirement.", (50, 280, 550, 330)
        ),
    ]
    units = paragraphs(blocks, [(600, 800)] * 2)
    assert [[i for i, _ in unit.parts] for unit in units] == [[0, 2], [1]]
    assert "historical information Can affect" in units[0].paragraph()
    assert "Figure" not in units[0].paragraph()


def test_sentence_continues_across_read_only_display_formula():
    first = block(0, "We measure the effect using the utility:", (50, 80, 550, 105))
    second = block(
        0, "within each cell, and average the resulting values.", (50, 160, 550, 200)
    )
    second.preceding_equation = "U = measured / baseline"
    units = paragraphs([first, second], [(600, 800)])
    assert len(units) == 1
    assert "U = measured / baseline" in units[0].paragraph()
    assert "read only" in units[0].paragraph()


def test_full_stop_protected_inside_math_artwork_still_ends_the_paragraph():
    first = block(
        0, "This entire paragraph explains the result {{math.0}}", (50, 675, 550, 735)
    )
    first.original_text = "This entire paragraph explains the result x = 1."
    second = block(1, "Another independent paragraph begins here.", (50, 80, 550, 130))
    assert len(paragraphs([first, second], [(600, 800)] * 2)) == 2


class Model:
    def __init__(self, result):
        self.settings = Settings(context_guidance=False)
        self.result = result
        self.payload = None
        self.messages = None

    async def complete(self, messages, **kwargs):
        assert kwargs["json_output"] is True
        self.messages = messages
        self.payload = json.loads(messages[1]["content"])
        return self.result(self.payload)


async def test_whole_paragraph_request_returns_translations_for_original_boxes():
    parts = crossed_page_blocks()
    unit = PDFParagraph([(1, parts[1]), (3, parts[3])])
    model = Model(
        lambda _: json.dumps(
            {
                "1": "即使其正效用已经衰减，历史影响仍然存在。",
                "3": "我们通过 ⟪B3P0000⟫ 次试验确认该结果。",
            }
        )
    )
    output, failures = await translate_paragraph(model, unit, "", {1, 3}, {})
    assert not failures and set(output) == {1, 3}
    assert "positive utility has decayed" in model.payload["paragraph"]
    assert "util-" in model.payload["parts"]["1"] and model.payload["parts"][
        "3"
    ].startswith("ity")
    assert "paper_context" not in model.payload
    assert parts[3].restore(output[3]) == "我们通过 8 次试验确认该结果。"
    assert "ity" not in "".join(output.values())


async def test_formula_and_number_tokens_from_different_regions_cannot_be_swapped():
    first = block(0, "The model has 12 layers and another result", protected=["12"])
    second = block(1, "continues with 8 trials and more information.", protected=["8"])
    unit = PDFParagraph([(0, first), (1, second)])
    model = Model(
        lambda _: json.dumps(
            {
                "0": "模型有 ⟪B1P0000⟫ 层，另一个结果",
                "1": "通过 ⟪B0P0000⟫ 次试验继续验证。",
            }
        )
    )
    outputs, errors = await translate_paragraph(model, unit, "", {0, 1}, {})
    assert not outputs and set(errors) == {0, 1}


async def test_repair_requests_only_invalid_regions_with_accepted_translation_as_context():
    parts = crossed_page_blocks()
    unit = PDFParagraph([(1, parts[1]), (3, parts[3])])
    model = Model(
        lambda _: json.dumps(
            {"1": "历史影响在正效用衰减后仍然存在。", "3": "模型漏掉了需要保护的数字。"}
        )
    )
    outputs, errors = await translate_paragraph(model, unit, "", {1, 3}, {})
    assert set(outputs) == {1} and set(errors) == {3}
    accepted = {parts[1].key: outputs[1]}
    model.result = lambda _: json.dumps({"3": "我们通过 ⟪B3P0000⟫ 次试验确认结果。"})
    repaired, errors = await translate_paragraph(
        model, unit, "", {3}, accepted, "数字缺失"
    )
    assert not errors and set(repaired) == {3}
    assert model.payload["requested_regions"] == ["3"]
    assert model.payload["accepted_translations"]["1"] == outputs[1]
    assert accepted[parts[1].key] == outputs[1]


@pytest.mark.parametrize(
    "result",
    ["not JSON", "[]", '{"1":"译文"}', '{"1":"译文","3":"译文","4":"多余文字"}'],
)
async def test_missing_or_extra_regions_are_rejected(result):
    parts = crossed_page_blocks()
    model = Model(lambda _: result)
    with pytest.raises(ValueError, match="JSON|文本区域"):
        await translate_paragraph(
            model, PDFParagraph([(1, parts[1]), (3, parts[3])]), "", {1, 3}, {}
        )
