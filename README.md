# ADA PDF Converter

Converts any PDF into a fully accessible, ADA-compliant PDF/UA-1 document.
Works on digital PDFs, scanned images, forms, and mixed documents.

---

## Quick Start

**Requirements:** Python 3.11+ · Internet connection (first run only)

### Mac
Double-click **`Run ADA PDF Converter.command`**
*(first time: right-click → Open to bypass Gatekeeper)*

### Windows
Double-click **`run.bat`**

### Terminal (Mac / Linux)
```bash
bash run.sh
```

**First run** downloads and installs all dependencies (~10-15 min, once only).  
**Every run after** starts in ~5 seconds.

The browser opens automatically to the UI.

---

## How It Works

1. Drop one or more PDFs onto the upload area
2. Choose conversion options (optional — auto-detect works for most PDFs)
3. Download the accessible PDF
4. Review the built-in PDF/UA-1 compliance report

---

## Folder Structure

```
ADA-PDF-creator/
├── run.sh / run.bat                 ← start here (auto-installs on first run)
├── Run ADA PDF Converter.command    ← Mac double-click launcher
│
├── frontend/
│   └── ADA_PDF_Converter.html       ← UI (opens automatically, or open manually)
│
└── backend/                         ← Python server (do not modify)
    ├── ada_pdf/                     ← pipeline and API
    ├── scripts/
    │   ├── install.sh / install.bat ← manual setup (run.sh calls this automatically)
    │   └── start.sh / start.bat     ← manual start
    ├── requirements.txt
    └── docker-compose.yml           ← optional Docker deployment
```

---

## What Gets Fixed Automatically

| Issue | Fix |
|-------|-----|
| Missing tags / structure | Full PDF/UA-1 tag tree added |
| Scanned pages with no text | OCR extracts all text |
| Images without descriptions | AI generates alt text |
| Missing document language | `/Lang` attribute added |
| Non-embedded fonts | System font equivalents embedded |
| Missing metadata | PDF/UA-1 XMP metadata written |
| Form fields without labels | Accessible labels added |
| Broken heading hierarchy | H1 → H2 → H3 order enforced |

---

## Validation

Every converted PDF is validated against the **PDF/UA-1 (ISO 14289-1)** standard using veraPDF.  
The result panel shows a compliance score and lists any remaining issues with fix suggestions.

Install [PAC 2024](https://www.pdfua.foundation/pac-2024/) for the most thorough validation check — the app detects it automatically.

---

## Advanced

**Run as a server (team use):**
```bash
cd backend
bash scripts/start.sh
```
Share the URL — anyone on the same network can open `frontend/ADA_PDF_Converter.html`
and point it at your machine's IP address.

**Docker (full stack with PostgreSQL + Celery):**
```bash
cd backend
cp .env.example .env   # edit with your settings
docker compose up
```

**Batch-fix existing PDFs:**
```bash
cd backend
python scripts/batch_evaluate_and_fix.py --input-dir path/to/pdfs
```

---

## Requirements

- Python 3.11 or newer
- ~3 GB disk space (for AI models, downloaded on first run)
- Internet connection on first run only
- Java 11+ (for veraPDF validation — downloaded automatically)
