"""Pre-download ML models at Docker build time to avoid cold-start delays."""
import os
import sys

print("Downloading ML models...")

# ── BLIP for alt text generation (free, CPU-capable) ──────────────────────
try:
    from transformers import BlipProcessor, BlipForConditionalGeneration
    print("Downloading BLIP image captioning model...")
    BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
    BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
    print("  ✓ BLIP downloaded")
except Exception as e:
    print(f"  ✗ BLIP download failed: {e}", file=sys.stderr)

# ── Surya OCR models ───────────────────────────────────────────────────────
try:
    from surya.model.recognition.model import load_model as load_rec_model
    from surya.model.recognition.processor import load_processor
    print("Downloading Surya recognition model...")
    load_rec_model()
    load_processor()
    print("  ✓ Surya recognition downloaded")
except Exception as e:
    print(f"  ✗ Surya recognition download failed: {e}", file=sys.stderr)

try:
    from surya.model.detection.model import load_model as load_det_model
    print("Downloading Surya detection model...")
    load_det_model()
    print("  ✓ Surya detection downloaded")
except Exception as e:
    print(f"  ✗ Surya detection download failed: {e}", file=sys.stderr)

# ── Docling models ─────────────────────────────────────────────────────────
try:
    from docling.document_converter import DocumentConverter
    print("Warming up Docling (this downloads layout models)...")
    # Docling downloads models on first use; trigger it now
    # by creating a converter instance (models loaded lazily)
    _ = DocumentConverter()
    print("  ✓ Docling initialized")
except Exception as e:
    print(f"  ✗ Docling init failed: {e}", file=sys.stderr)

print("Model download complete.")
