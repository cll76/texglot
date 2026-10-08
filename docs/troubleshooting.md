# Scope and troubleshooting

**English** · [简体中文](troubleshooting_CN.md) · [Back to README](../README.md)

TeXGlot translates **LaTeX source and local text PDFs**. An arXiv PDF URL identifies downloadable LaTeX source. Local PDFs use extractable text and preserve layout, images and formulas where possible; scanned documents need OCR first. PDF input needs no TeX compiler and does not generate LaTeX source. The Zotero plugin still submits only arXiv or LaTeX inputs.

| Situation | What to check |
| :--- | :--- |
| arXiv import fails | Confirm that the paper has downloadable TeX source and that arXiv is reachable. |
| Local PDF import fails | Check that the file is readable, unencrypted and contains extractable text. Run OCR on scans first. |
| PDF translation cannot fit | Text can shrink moderately within the original region. If it still cannot fit, keep the original text and report a partial result. |
| EPS figures | Install optional Ghostscript and resume. Original EPS files are retained; PDF copies are generated for compilation. See the [desktop guide](desktop.md). |
| PSTricks / PostScript drawings | The default Tectonic bundle does not provide a full PostScript toolchain. Prefer pre-converted PDF figures. A separate full TeX setup needs the matching driver and external tools; verify the drawings as well as the exit status. |
| Compilation fails | Include missing `.sty`, images and bibliography files; inspect the compile log. Templates needing shell escape or unavailable external tools may not work. An installed XeLaTeX/LuaLaTeX can be selected in settings. |
| `sandbox_apply: Operation not permitted` | A service launched from a restricted development tool may be unable to create a macOS compiler sandbox. Stop it, run `bash start-texglot.command` in the system Terminal, and resume the existing job. Compiler isolation is retained; compilation does not automatically fall back to running without a sandbox. |
| API errors | Check the endpoint, region, model access, balance and rate limits. Reduce concurrency or increase timeout when needed. |
| Some prose stays untranslated | Review task warnings and resume to retry failed segments. Unknown macros, authors and bibliography entries may intentionally remain unchanged. |
| Different page counts | Translation changes text length and pagination. Synchronization uses shared content landmarks where available; otherwise it falls back to page-local positions. It is not sentence-level alignment. |
| Source installation times out during frontend build | The local build stops after 10 minutes. Inspect the output; if directory access is hanging, extract the source into an ordinary local project folder and rerun setup. Dependency downloads are not subject to this build timeout. |
| `texglot` is missing | Reopen the terminal after `uv tool update-shell`, or use `uv run python -m app.cli` in the checkout. |

Text inside bitmap and vector illustrations stays unchanged. Images are not OCR-translated or redrawn; surrounding prose and captions are translated. Model output still needs human review for research use. No automatic check proves semantic correctness for every sentence.
