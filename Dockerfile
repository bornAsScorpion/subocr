# syntax=docker/dockerfile:1

# --- Build stage: compile subtile-ocr against Debian bookworm's Tesseract/Leptonica ---
FROM rust:1-bookworm AS ocr-build
RUN apt-get update \
 && apt-get install -y --no-install-recommends libtesseract-dev libleptonica-dev clang pkg-config \
 && rm -rf /var/lib/apt/lists/*
ARG SUBTILE_OCR_VERSION=0.2.6
RUN cargo install --locked subtile-ocr --version "${SUBTILE_OCR_VERSION}" --root /out

# --- Runtime: same Debian release, so the shared libraries match ---
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="subocr" \
      org.opencontainers.image.description="Fills subtitle gaps next to your media: extracts embedded text subtitles and OCRs image-based ones to SRT." \
      org.opencontainers.image.licenses="MIT"

# Tesseract models baked into the image. Models for other LANGUAGES download at
# startup into TESSDATA_DIR; bake more in here for hosts without internet access.
ARG TESSERACT_LANGS="eng"
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg mkvtoolnix tesseract-ocr tzdata \
      $(for l in ${TESSERACT_LANGS}; do printf 'tesseract-ocr-%s ' "$l"; done) \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ocr-build /out/bin/subtile-ocr /usr/local/bin/subtile-ocr

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /config /scratch
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

ENV CONFIG_DIR=/config \
    TESSDATA_DIR=/config/tessdata \
    SCRATCH_DIR=/scratch \
    PORT=8585 \
    PYTHONUNBUFFERED=1
VOLUME ["/config", "/scratch"]
EXPOSE 8585

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8585'), timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh", "subocr"]
CMD ["serve"]
