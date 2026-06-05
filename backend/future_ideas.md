# Future Ideas & Planned Enhancements

## PyInstaller Portable Binary

Package the entire ADA PDF Creator — FastAPI server, Celery worker, Redis, veraPDF, all Python dependencies, and the UI — into a single self-contained executable per platform (`.exe` for Windows, `.app` for macOS, Linux binary).

**Goal:** Double-click to run. No Python, no Docker, no terminal required.

**Challenges to solve:**
- PyTorch / Surya OCR models are large (~1–2 GB); need a first-run download strategy
- Redis must be bundled or replaced with an in-process alternative (e.g., `fakeredis`)
- PostgreSQL dependency should be swapped for SQLite for the embedded case
- veraPDF requires a bundled JRE
- WeasyPrint has native GTK dependencies on Linux/Windows that need static linking
- Code-signing and notarization required for macOS distribution

**Rough size estimate:** 3–5 GB installer; ~1.5 GB compressed

**Suggested approach:**
1. Swap Redis → `fakeredis` (in-memory, no server needed)
2. Swap PostgreSQL → SQLite + `aiosqlite`
3. Bundle veraPDF + AdoptOpenJDK JRE 17
4. Use PyInstaller `--onedir` (not `--onefile`) to avoid slow extraction
5. Wrap in a platform installer (NSIS for Windows, `create-dmg` for macOS)
6. On first launch, download Surya/BLIP models to `~/.ada-pdf-creator/models/`

---

## Language Override (Primary Language Selector)

A language selector existed in the UI but `force_language` was stored in the pipeline context and never read — every pipeline run auto-detects language via `lingua` in `s5_structure.py`.

**To implement properly:**
- `s5_structure.py` should check `ctx.force_language` before calling `_detect_language()` and skip detection if a language is forced
- `s3_ocr.py` (Surya/Tesseract) should pass the forced language code to the OCR engine for improved accuracy on non-English scanned docs
- Forced language should also override the `/Lang` tag written to the PDF in `s8_metadata.py`

---

## Form Field Detection Toggle

A "Detect and label form fields" toggle existed in the UI but `ctx.skip_forms` was never read — `s5_structure.py` always called `_extract_form_fields()` regardless of the toggle.

**To implement properly:**
- `s5_structure.py` should check `ctx.skip_forms` and skip the `_extract_form_fields()` / `_attach_form_blocks()` calls when the toggle is off
- `s8_metadata.py` should also skip `_fix_form_field_labels()`, `_add_widget_form_struct_elements()`, and `_add_link_struct_elements()` when forms are skipped

---

## Self-Contained HTML + Remote Backend (VPS / Cloud)

Generate a single `.html` file with all CSS, JavaScript, and fonts inlined. The file includes a configurable server URL field so it can point at any deployed instance of the ADA PDF Creator backend.

**Goal:** Share one HTML file with anyone. They open it in a browser, type in the server address, and use the full converter — no installation on their machine.

**How it works:**
- The HTML file is purely a browser UI — no Python, no Node, nothing to install
- All conversion still happens on the remote backend (FastAPI + Celery + Redis + PostgreSQL)
- The backend is deployed once on a VPS, cloud VM, or office server
- Users receive the `.html` file and the server URL; that's all they need

**What needs to be built:**
- A build script (`scripts/build_standalone_html.py`) that inlines style.css, app.js, jszip.min.js, and the Inter font (base64) into a single HTML file
- A server URL configuration field in the UI (pre-filled, editable) so the file can be aimed at any deployment
- CORS configuration on the backend to allow requests from `file://` origins (or from the domain hosting the HTML)
- API key input field in the HTML so users can authenticate without hardcoding credentials

---

## Fast vs. Thorough Processing Modes

The UI previously offered a "Fast" vs "Thorough" toggle but both ran identical pipelines — the setting was stored but never read. The toggle was removed to avoid confusion.

**Future implementation:** make the distinction real.

- **Thorough (current behavior):** full Surya OCR on every scanned page, BLIP-2 alt text for all images, AI layout analysis for column detection, full veraPDF validation
- **Fast:** skip BLIP-2 alt text (biggest time cost for image-heavy PDFs), use gap-based column detector only (no k-means fallback), raise the digital-page text threshold to skip OCR on lightly-scanned pages, skip veraPDF validation pass

Expected speedup on a typical mixed PDF: ~3–5× faster in fast mode. Accuracy tradeoff: alt text will be absent; column order may be wrong on complex layouts.

---

**Deployment sketch:**
1. Deploy backend on a VPS (e.g., DigitalOcean, AWS EC2, Hetzner)
2. Run `python scripts/build_standalone_html.py --server https://ada.yourcompany.com` → produces `ADA_PDF_Converter.html`
3. Distribute that file to users — no other files needed on their machines
