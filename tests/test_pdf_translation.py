import asyncio
import io
import json
import re
import unicodedata

import httpx
import pymupdf
import pytest
from PIL import Image

from app import jobs
from app.llm import ProviderError
from app.pdf_translation import extract_blocks, open_pdf, render_translation
from app.reader import ReaderStore


def paper(path, rotation=0):
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((45, 60), "Local paper", fontsize=18)
        page.insert_textbox(
            pymupdf.Rect(45, 90, 285, 165),
            "The model uses 12 layers in the experiment. We confirm the scientific result.",
            fontsize=12,
        )
        page.insert_textbox(
            pymupdf.Rect(325, 90, 560, 165),
            "Our second column uses 8 layers and reports reliable results.",
            fontsize=12,
        )
        prefix = "Our equation uses "
        page.insert_text((45, 200), prefix, fontsize=12)
        x = 45 + pymupdf.get_text_length(prefix, fontsize=12)
        page.insert_text((x, 200), "a+b", fontsize=12, fontname="symb")
        page.insert_text((x + 32, 200), " in this experiment.", fontsize=12)
        page.insert_text((45, 255), "a+b=c", fontsize=14, fontname="symb")
        page.draw_rect(
            pymupdf.Rect(330, 190, 550, 320), color=(0, 0, 1), fill=(0.9, 0.95, 1)
        )
        image = Image.new("RGB", (64, 48), (180, 50, 90))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        page.insert_image(pymupdf.Rect(50, 350, 200, 465), stream=stream.getvalue())
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(45, 45, 155, 65),
                "uri": "https://example.com/paper",
            }
        )
        page.set_rotation(rotation)
        document.save(path)


def translated(block):
    return re.sub(r"[A-Za-z]{2,}", "译文", block.masked)


@pytest.mark.parametrize("rotation", [0, 90])
async def test_layout_images_vectors_formula_and_links_survive(tmp_path, rotation):
    original = tmp_path / "original.pdf"
    target = tmp_path / "translated.pdf"
    paper(original, rotation)
    before = original.read_bytes()
    with open_pdf(original) as document:
        blocks, empty_pages = extract_blocks(document, tmp_path / "assets")
        assert not empty_pages
        assert any("second column" in block.source for block in blocks)
        assert not any(
            "second column" in block.source and "scientific result" in block.source
            for block in blocks
        )
        assert any(block.formulas for block in blocks)
        assert not any(block.source == "a+b=c" for block in blocks)
        source_page = document[0]
        source_page.set_rotation(0)
        picture = source_page.get_pixmap(clip=pymupdf.Rect(50, 350, 200, 465)).samples
        drawing = source_page.get_pixmap(clip=pymupdf.Rect(330, 190, 550, 320)).samples
        equation = source_page.get_pixmap(clip=pymupdf.Rect(40, 235, 130, 265)).samples
    outputs = {block.key: translated(block) for block in blocks}
    assert (
        await render_translation(original, target, blocks, outputs, tmp_path / "assets")
        == []
    )
    assert original.read_bytes() == before
    with open_pdf(target) as document:
        assert document.page_count == 1 and document[0].rotation == rotation
        page = document[0]
        page.set_rotation(0)
        text = unicodedata.normalize("NFKC", page.get_text())
        assert "译文" in text and "Local paper" not in text
        assert "12" in text and "8" in text
        assert page.get_pixmap(clip=pymupdf.Rect(50, 350, 200, 465)).samples == picture
        assert page.get_pixmap(clip=pymupdf.Rect(330, 190, 550, 320)).samples == drawing
        assert page.get_pixmap(clip=pymupdf.Rect(40, 235, 130, 265)).samples == equation
        assert page.get_links()[0]["uri"] == "https://example.com/paper"


async def test_unfittable_translation_keeps_original_text(tmp_path):
    source, target = tmp_path / "original.pdf", tmp_path / "translated.pdf"
    paper(source)
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
    block = blocks[0]
    outputs = {block.key: "译文" * 3000}
    skipped = await render_translation(
        source, target, blocks, outputs, tmp_path / "assets"
    )
    assert skipped == [0]
    with open_pdf(target) as document:
        assert "Local paper" in document[0].get_text()


async def test_zero_width_tex_negation_is_cropped_with_its_relation(
    tmp_path, monkeypatch
):
    source, target = tmp_path / "source.pdf", tmp_path / "translated.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=200)
        page.insert_text((35, 70), "Our result", fontsize=12)
        page.insert_text((135, 70), "=", fontsize=12)
        page.draw_line((132, 63), (145, 75))
        document.save(source)
    with open_pdf(source) as document:
        original_formula = (
            document[0]
            .get_pixmap(
                matrix=pymupdf.Matrix(3, 3), clip=pymupdf.Rect(130, 57, 147, 78)
            )
            .samples
        )
        # TeX produces a zero-advance combining slash followed by a space and '='.
        # The slash is visible ink even though its extracted span has zero width.
        data = {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "dir": (1.0, 0.0),
                            "spans": [
                                {
                                    "text": "Our result ",
                                    "font": "CMR10",
                                    "size": 12,
                                    "bbox": (35, 57, 120, 78),
                                    "color": 0,
                                    "flags": 0,
                                },
                                {
                                    "text": "\u0338",
                                    "font": "CMSY10",
                                    "size": 12,
                                    "bbox": (130, 57, 130, 78),
                                    "color": 0,
                                    "flags": 0,
                                },
                                {
                                    "text": " ",
                                    "font": "CMR10",
                                    "size": 12,
                                    "bbox": (130, 57, 135, 78),
                                    "color": 0,
                                    "flags": 0,
                                },
                                {
                                    "text": "=",
                                    "font": "CMR10",
                                    "size": 12,
                                    "bbox": (135, 57, 147, 78),
                                    "color": 0,
                                    "flags": 0,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        with monkeypatch.context() as patch:
            patch.setattr(pymupdf.Page, "get_text", lambda *_args, **_kwargs: data)
            blocks, _ = extract_blocks(document, tmp_path / "assets")
    assert len(blocks) == 1 and len(blocks[0].formulas) == 1
    filename, width, height = next(iter(blocks[0].formulas.values()))
    assert width == 17 and height == 21
    crop = pymupdf.Pixmap(str(tmp_path / "assets" / filename))
    assert crop.width > 0 and crop.height > 0 and crop.samples == original_formula
    skipped = await render_translation(
        source, target, blocks, {blocks[0].key: "结果 ⟪P0000⟫"}, tmp_path / "assets"
    )
    assert not skipped


def test_standalone_zero_width_math_is_kept_without_generating_an_image(
    tmp_path, monkeypatch
):
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=200)
        page.insert_text((35, 45), "Paper result", fontsize=12)
        data = page.get_text("dict")
        data["blocks"].append(
            {
                "type": 0,
                "lines": [
                    {
                        "dir": (1.0, 0.0),
                        "spans": [
                            {
                                "text": "\u0338",
                                "font": "CMSY10",
                                "size": 12,
                                "bbox": (35, 60, 35, 72),
                                "color": 0,
                                "flags": 0,
                            },
                        ],
                    }
                ],
            }
        )
        with monkeypatch.context() as patch:
            patch.setattr(pymupdf.Page, "get_text", lambda *_args, **_kwargs: data)
            blocks, empty_pages = extract_blocks(document, tmp_path / "assets")
    assert len(blocks) == 1 and blocks[0].source == "Paper result"
    assert not empty_pages and not list((tmp_path / "assets").glob("*.png"))


async def test_ocr_text_is_replaced_without_overlaying_scanned_words(tmp_path):
    source = tmp_path / "ocr.pdf"
    target = tmp_path / "translated.pdf"
    with pymupdf.open() as scan:
        page = scan.new_page(width=600, height=800)
        page.insert_text((45, 100), "Our model confirms the result.", fontsize=16)
        page.draw_rect(pymupdf.Rect(45, 170, 250, 300), fill=(0.8, 0.9, 1))
        image = page.get_pixmap()
        with pymupdf.open() as ocr:
            page = ocr.new_page(width=600, height=800)
            page.insert_image(page.rect, pixmap=image)
            page.insert_text(
                (45, 100), "Our model confirms the result.", fontsize=16, render_mode=3
            )
            ocr.save(source)
    with open_pdf(source) as document:
        blocks, _ = extract_blocks(document, tmp_path / "assets")
        picture = document[0].get_pixmap(clip=pymupdf.Rect(45, 170, 250, 300)).samples
    await render_translation(
        source,
        target,
        blocks,
        {b.key: translated(b) for b in blocks},
        tmp_path / "assets",
    )
    with open_pdf(target) as document:
        page = document[0]
        assert "Our model" not in page.get_text()
        assert "译文" in page.get_text()
        assert page.get_pixmap(clip=pymupdf.Rect(45, 170, 250, 300)).samples == picture
        # A white cover removes the scanned Latin glyphs beneath the translation.
        pix = page.get_pixmap(clip=pymupdf.Rect(45, 104, 250, 108))
        assert all(value == 255 for value in pix.samples)


@pytest.mark.parametrize("input_kind", ["scan", "encrypted", "invalid"])
def test_unsupported_pdf_is_actionable_before_any_model_call(tmp_path, input_kind):
    path = tmp_path / "input.pdf"
    if input_kind == "invalid":
        path.write_bytes(b"not a PDF")
    else:
        with pymupdf.open() as document:
            document.new_page()
            if input_kind == "encrypted":
                document.save(
                    path,
                    encryption=pymupdf.PDF_ENCRYPT_AES_256,
                    owner_pw="owner",
                    user_pw="user",
                )
            else:
                document.save(path)
    with pytest.raises(
        ValueError,
        match={"scan": "OCR", "encrypted": "密码", "invalid": "读取"}[input_kind],
    ):
        with open_pdf(path) as document:
            extract_blocks(document, tmp_path / "assets")


@pytest.fixture
def pdf_manager(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "CONFIG", tmp_path / "settings.json")
    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(jobs, "JOBS", root)
    manager = jobs.JobManager()

    def no_latex(*_):
        raise AssertionError("PDF translation must not need a LaTeX compiler")

    monkeypatch.setattr(jobs, "choose_compiler", no_latex)
    return manager, root


class FakeModel:
    def __init__(self, settings):
        self.settings = settings
        self.tokens = 0
        self.requests = 0
        self.closed = False

    async def complete(self, messages, **kwargs):
        self.requests += 1
        self.tokens += 15
        data = json.loads(messages[1]["content"])
        if "requested_regions" in data:
            return json.dumps(
                {
                    index: re.sub(r"[A-Za-z]{2,}", "译文", data["parts"][index])
                    for index in data["requested_regions"]
                }
            )
        return re.sub(r"[A-Za-z]{2,}", "译文", data["paragraph"])

    async def close(self):
        self.closed = True


async def test_grouped_pdf_pipeline_retranslates_only_changed_paragraph_context(
    pdf_manager, monkeypatch, tmp_path
):
    import app.pdf_translation as pdf
    from app.pdf_paragraphs import paragraphs

    manager, root = pdf_manager
    source = tmp_path / "continuous.pdf"
    with pymupdf.open() as document:
        first = document.new_page(width=600, height=800)
        first.insert_text((50, 35), "Under review as a conference paper", fontsize=12)
        first.insert_textbox(
            pymupdf.Rect(50, 675, 550, 740),
            "We observe that historical influence persists even after its positive util-",
            fontsize=12,
        )
        second = document.new_page(width=600, height=800)
        second.insert_text((50, 35), "Under review as a conference paper", fontsize=12)
        second.insert_textbox(
            pymupdf.Rect(50, 80, 550, 150),
            "ity has decayed. We confirm this scientific result after 8 trials.",
            fontsize=12,
        )
        document.save(source)
    instances = []

    def translator(settings):
        model = FakeModel(settings)
        instances.append(model)
        return model

    monkeypatch.setattr(pdf, "Translator", translator)
    job = manager.create(
        "pdf", "continuous.pdf", blob=source.read_bytes(), context_guidance=False
    )
    await manager.tasks[job["id"]]
    assert job["status"] == "completed" and job["total"] == 4
    assert job["translation_units"] == 3 and instances[0].requests == 3
    folder = root / job["id"]
    with open_pdf(folder / "original.pdf") as document:
        sizes = [(page.rect.width, page.rect.height) for page in document]
        blocks, _ = extract_blocks(document, folder / "pdf-assets")
    units = paragraphs(blocks, sizes)
    joined = next(unit for unit in units if len(unit.parts) > 1)
    cache_file = next(folder.glob("pdf-cache-*.json"))
    cache = json.loads(cache_file.read_text())
    for index, b in joined.parts:
        cache.pop(joined.cache_key(index, b))
        cache[b.key] = translated(
            b
        )  # A valid translation made without paragraph context.
    cache_file.write_text(json.dumps(cache), encoding="utf-8")
    manager.start(job["id"])
    await manager.tasks[job["id"]]
    assert job["status"] == "completed" and job["cached"] == 2
    assert instances[1].requests == 1
    manager.start(job["id"])
    await manager.tasks[job["id"]]
    assert job["cached"] == 4 and instances[2].requests == 0
    assert (folder / "original.pdf").read_bytes() == source.read_bytes()
    await manager.close()


async def test_pdf_api_pipeline_reader_exports_and_cache_resume(
    pdf_manager, monkeypatch, tmp_path
):
    import app.main as main
    import app.pdf_translation as pdf

    manager, root = pdf_manager
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setattr(main, "JOBS", root)
    monkeypatch.setattr(main, "reader_store", ReaderStore(root))
    instances = []

    def translator(settings):
        model = FakeModel(settings)
        instances.append(model)
        return model

    monkeypatch.setattr(pdf, "Translator", translator)
    source = tmp_path / "source.pdf"
    paper(source)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/api/jobs/file",
            files={"file": ("local.PDF", source.read_bytes(), "application/pdf")},
            data={"context_guidance": "false"},
        )
        assert created.status_code == 202
        job = manager.get(created.json()["id"])
        await manager.tasks[job["id"]]
        assert job["kind"] == "pdf" and job["status"] == "completed"
        assert job["pages"] == 1 and instances[0].requests > 0 and instances[0].closed
        original = await client.get(f"/api/jobs/{job['id']}/artifacts/original")
        assert original.content == source.read_bytes()
        output = await client.get(f"/api/jobs/{job['id']}/artifacts/translated")
        assert output.status_code == 200 and output.content.startswith(b"%PDF")
        reader = await client.get(f"/api/jobs/{job['id']}/reader")
        assert reader.status_code == 200
        assert reader.json()["documents"]["translated"]["pages"] == 1
        assert "source" not in job["artifacts"]
        retry = await client.post(f"/api/jobs/{job['id']}/retry", json={})
        assert retry.status_code == 200
        await manager.tasks[job["id"]]
        assert job["status"] == "completed" and job["cached"] == job["total"]
        assert instances[1].requests == 0 and instances[1].closed
        assert job["tokens"] == instances[0].tokens
        cache = next((root / job["id"]).glob("pdf-cache-*.json"))
        cache.write_text("{broken", encoding="utf-8")
        manager.start(job["id"])
        await manager.tasks[job["id"]]
        assert job["status"] == "completed" and instances[2].requests > 0
        assert list((root / job["id"]).glob("pdf-cache-*-invalid-*.json"))
    await manager.close()


async def test_failed_pdf_blocks_are_partial_and_cancellation_closes_model(
    pdf_manager, monkeypatch, tmp_path
):
    import app.pdf_translation as pdf

    manager, root = pdf_manager
    source = tmp_path / "source.pdf"
    paper(source)
    models = []
    mode = "bad"
    entered = asyncio.Event()
    hold = asyncio.Event()

    class Model(FakeModel):
        async def complete(self, *args, **kwargs):
            if mode == "bad":
                return ""
            entered.set()
            await hold.wait()
            raise ProviderError("Stopped")

    def translator(settings):
        model = Model(settings)
        models.append(model)
        return model

    monkeypatch.setattr(pdf, "Translator", translator)
    job = manager.create("pdf", "input.pdf", blob=source.read_bytes())
    await manager.tasks[job["id"]]
    assert job["status"] == "partial" and job["warnings"]
    assert models[0].closed
    mode = "hold"
    manager.start(job["id"])
    await entered.wait()
    await manager.cancel(job["id"])
    assert job["status"] == "cancelled" and models[1].closed
    assert not manager.slot.locked()
    await manager.close()


def test_cli_accepts_local_pdf(tmp_path):
    from app.cli import Service, submit

    source = tmp_path / "paper.pdf"
    paper(source)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={"id": "test"})

    service = Service(transport=httpx.MockTransport(handler))
    try:
        assert submit(service, str(source), "English")["id"] == "test"
        assert calls[0].url.path == "/api/jobs/file"
        assert source.read_bytes() in calls[0].content
    finally:
        service.client.close()
