import pymupdf
import pytest

from app.pdf_translation import extract_blocks, open_pdf, render_translation


@pytest.mark.parametrize("kind", ["bitmap", "vectors", "placed-vectors"])
async def test_artwork_labels_stay_original_while_body_and_caption_translate(
    tmp_path, kind
):
    source, target = tmp_path / "source.pdf", tmp_path / "translated.pdf"
    graphic = pymupdf.open()
    try:
        plot = graphic.new_page(width=240, height=120)
        plot.draw_rect(
            pymupdf.Rect(2, 2, 238, 118), color=(0, 0, 1), fill=(0.9, 0.95, 1)
        )
        plot.insert_text((25, 50), "Agent", fontsize=14)
        plot.insert_text((140, 80), "Artifact", fontsize=14)
        with pymupdf.open() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text(
                (40, 60), "The experiment confirms our result.", fontsize=12
            )
            area = pymupdf.Rect(40, 100, 280, 220)
            if kind == "bitmap":
                page.insert_image(area, pixmap=plot.get_pixmap())
                # Some figures also have selectable/OCR text over their pixels.
                page.insert_text((65, 150), "Agent", fontsize=14, render_mode=3)
            elif kind == "placed-vectors":
                page.show_pdf_page(area, graphic)
            else:
                page.draw_rect(area, color=(0, 0, 1), fill=(0.9, 0.95, 1))
                page.insert_text((65, 150), "Agent", fontsize=14)
                page.insert_text((180, 180), "Artifact", fontsize=14)
            page.insert_text(
                (40, 250), "Figure 1: The collaboration diagram.", fontsize=12
            )
            document.save(source)
    finally:
        graphic.close()
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
        original_picture = document[0].get_pixmap(clip=area).samples
    assert len(blocks) == 2
    assert blocks[0].source.startswith("The experiment")
    assert blocks[1].source.startswith("Figure 1:")
    assert not any(
        "Agent" in block.source or "Artifact" in block.source for block in blocks
    )
    outputs = {
        blocks[0].key: "实验证实了我们的结果。",
        blocks[1].key: "图 ⟪P0000⟫：协作示意图。",
    }
    assert not await render_translation(
        source, target, blocks, outputs, tmp_path / "assets"
    )
    with open_pdf(target) as document:
        text = document[0].get_text()
        assert "实验证实" in text and "协作示意图" in text
        assert document[0].get_pixmap(clip=area).samples == original_picture
        if kind != "bitmap":
            assert "Agent" in text and "Artifact" in text


def test_text_only_placed_pdf_is_body_not_a_picture(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as text_only:
        text_only.new_page(width=240, height=120).insert_text(
            (15, 30), "Ordinary body text", fontsize=12
        )
        with pymupdf.open() as document:
            document.new_page(width=600, height=800).show_pdf_page(
                pymupdf.Rect(40, 100, 280, 220), text_only
            )
            document.save(source)
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
    assert len(blocks) == 1 and blocks[0].source == "Ordinary body text"


def test_native_table_text_is_not_excluded_as_a_vector_figure(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 80), "Table 1: Experimental results.", fontsize=12)
        page.draw_rect(pymupdf.Rect(40, 100, 280, 200))
        page.draw_line((40, 150), (280, 150))
        page.draw_line((160, 100), (160, 200))
        page.insert_text((50, 130), "Model", fontsize=12)
        page.insert_text((175, 130), "Accuracy", fontsize=12)
        page.insert_text((50, 180), "Baseline", fontsize=12)
        page.insert_text((175, 180), "92.5", fontsize=12)
        document.save(source)
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
    values = [block.source for block in blocks]
    assert "Model" in values and "Accuracy" in values and "Baseline" in values


async def test_picture_only_task_makes_no_model_requests(monkeypatch, tmp_path):
    from app import config, jobs
    from app import pdf_translation as pdf

    monkeypatch.setattr(config, "CONFIG", tmp_path / "settings.json")
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(jobs, "JOBS", root)
    manager = jobs.JobManager()
    source = tmp_path / "diagram.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_rect(pymupdf.Rect(40, 100, 400, 300))
        page.insert_text((70, 180), "Keep this diagram label unchanged", fontsize=12)
        document.save(source)

    class Model:
        tokens = 0

        def __init__(self, settings):
            pass

        async def complete(self, *_args, **_kwargs):
            raise AssertionError("Figure text must not be sent to the model")

        async def close(self):
            pass

    monkeypatch.setattr(pdf, "Translator", Model)
    job = manager.create("pdf", "diagram.pdf", blob=source.read_bytes())
    await manager.tasks[job["id"]]
    assert job["status"] == "completed" and job["total"] == 0 and job["tokens"] == 0
    with (
        open_pdf(source) as original,
        open_pdf(root / job["id"] / "translated.pdf") as result,
    ):
        assert original[0].get_pixmap().samples == result[0].get_pixmap().samples
    await manager.close()
