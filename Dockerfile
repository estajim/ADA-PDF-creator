# ── Stage 1: builder ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# System dependencies: Tesseract OCR + Java for veraPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ara \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    default-jre-headless \
    libmagic1 \
    libglib2.0-0 \
    libgl1-mesa-glx \
    wget \
    unzip \
 && rm -rf /var/lib/apt/lists/*

# Download veraPDF
RUN mkdir -p /opt/verapdf && \
    wget -q "https://software.verapdf.org/releases/1.26/verapdf-greenfield-1.26.2-installer.zip" \
         -O /tmp/verapdf.zip && \
    unzip -q /tmp/verapdf.zip -d /tmp/verapdf_inst && \
    sh /tmp/verapdf_inst/verapdf-greenfield-*/install.sh -q && \
    rm -rf /tmp/verapdf.zip /tmp/verapdf_inst

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download ML models at build time (avoids cold-start delays)
COPY scripts/download_models.py scripts/download_models.py
RUN python scripts/download_models.py || echo "Model download skipped or failed — will retry at runtime"

# ── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.11-slim

# Copy only what we need from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/bin/tesseract /usr/bin/tesseract
COPY --from=builder /usr/share/tesseract-ocr /usr/share/tesseract-ocr
COPY --from=builder /usr/bin/java /usr/bin/java
COPY --from=builder /usr/lib/jvm /usr/lib/jvm
COPY --from=builder /opt/verapdf /opt/verapdf
COPY --from=builder /root/.cache /root/.cache
COPY --from=builder /usr/lib/x86_64-linux-gnu/libmagic* /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libglib* /usr/lib/x86_64-linux-gnu/

# Runtime system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN mkdir -p /app/storage /app/models

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VERAPDF_CLI_PATH=/opt/verapdf/verapdf

EXPOSE 8000

CMD ["uvicorn", "ada_pdf.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
