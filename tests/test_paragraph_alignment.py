from pathlib import Path

import pymupdf
import pytest

from app.alignment import build_alignment
from app.paragraph_alignment import TextPositions
from app.reader import ReaderStore


def text_pdf(path, count, blocks, *, chinese=False):
    with pymupdf.open() as document:
        for _ in range(count):
            document.new_page(width=600, height=900)
        for page, top, text in blocks:
            remaining = document[page - 1].insert_textbox(
                pymupdf.Rect(40, top, 560, 860),
                text,
                fontsize=10,
                fontname="china-s" if chinese else "helv",
            )
            assert remaining >= 0
        document.save(path)


def project(folder: Path, source: str, translation: str):
    for name, text in (("prepared-source", source), ("translated", translation)):
        root = folder / name
        root.mkdir(parents=True)
        (root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            + text
            + "\n\\end{document}",
            encoding="utf-8",
        )


def test_reader_matches_paragraphs_across_different_pages_and_local_lengths(tmp_path):
    folder = tmp_path / "paper"
    source = [
        "Warehouse delivery relies on extensive context retrieval and validation.",
        "Execution repairs missing dependencies before submitting the workflow.",
    ]
    translation = [
        "数据仓库交付依赖广泛的上下文检索和验证，并由多个平台协作完成。",
        "执行阶段会在提交工作流之前修复缺失的依赖关系。",
    ]
    project(folder, "\n\n".join(source), "\n\n".join(translation))
    text_pdf(folder / "original.pdf", 4, [(2, 180, source[0]), (4, 540, source[1])])
    text_pdf(
        folder / "translated.pdf",
        3,
        [(1, 360, translation[0]), (3, 120, translation[1])],
        chinese=True,
    )
    job = {
        "id": folder.name,
        "artifacts": {"original": "original.pdf", "translated": "translated.pdf"},
        "translation_files": ["main.tex"],
        "source_dependencies": ["main.tex"],
    }
    alignment = ReaderStore(tmp_path).get(job)["alignment"]
    assert alignment["kind"] == "landmarks"
    starts = [pair for pair in alignment["pairs"] if pair["id"].endswith(":start")]
    assert len(starts) == 2
    assert [pair["original"]["page"] for pair in starts] == [2, 4]
    assert [pair["translated"]["page"] for pair in starts] == [1, 3]
    assert starts[0]["original"]["fraction"] == pytest.approx(0.2, abs=0.01)
    assert starts[0]["translated"]["fraction"] == pytest.approx(0.4, abs=0.01)
    assert starts[1]["original"]["fraction"] == pytest.approx(0.6, abs=0.01)
    assert starts[1]["translated"]["fraction"] == pytest.approx(120 / 900, abs=0.01)


def test_long_source_does_not_split_against_a_shorter_translation(tmp_path):
    source = (
        "A unique opening describes the production warehouse environment. "
        + "Repeated context illustrates the delivery process. " * 100
        + "A distinct final sentence concludes this complete paragraph."
    )
    translation = "这一完整段落描述生产数据仓库中的任务交付流程，并以独特的总结句结束。"
    assert len(source) > 4500
    project(tmp_path, source, translation)
    text_pdf(tmp_path / "original.pdf", 1, [(1, 40, source)])
    text_pdf(tmp_path / "translated.pdf", 1, [(1, 180, translation)], chinese=True)
    alignment = build_alignment(
        tmp_path / "original.pdf", tmp_path / "translated.pdf", {}, ("main.tex",)
    )
    assert {pair["id"] for pair in alignment["pairs"]} == {
        "paragraph:main.tex:0:start",
        "paragraph:main.tex:0:end",
    }


def test_line_wraps_and_hyphens_match_but_repeated_prose_is_ambiguous(tmp_path):
    path = tmp_path / "text.pdf"
    duplicate = "Repeated information appears in multiple paragraphs."
    text_pdf(
        path,
        2,
        [
            (1, 100, "Warehouse work-\nflows retrieve context before execution."),
            (1, 300, duplicate),
            (2, 300, duplicate),
        ],
    )
    positions = TextPositions(path)
    assert positions.find("Warehouse workflows retrieve context before execution.")
    assert positions.find(duplicate) is None
    assert positions.find("Text that is absent from the document.") is None


def test_changed_source_structure_does_not_pair_unrelated_paragraphs(tmp_path):
    source = "An original paragraph describing the delivery process."
    translation = "译文的第一段描述数据仓库中的任务交付流程。\n\n译文额外增加一段关于执行步骤的描述。"
    project(tmp_path, source, translation)
    text_pdf(tmp_path / "original.pdf", 1, [(1, 100, source)])
    text_pdf(tmp_path / "translated.pdf", 1, [(1, 100, translation)], chinese=True)
    alignment = build_alignment(
        tmp_path / "original.pdf", tmp_path / "translated.pdf", {}, ("main.tex",)
    )
    assert alignment["pairs"] == []
