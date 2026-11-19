# LightPhotoLibrary server image (runs on the NAS)
# Build:  docker build -t lightphotolibrary .
# Works on x86-64 and ARM64 NAS models.

# the web app is plain JS/CSS, so build it once on the build machine's own CPU
FROM --platform=$BUILDPLATFORM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PHOTO_ROOT=/photos DATA_DIR=/data WEB_DIR=/app/web_dist PORT=8080
# tzdata: the time zones, so TZ in docker-compose.yml works (slim images may come without them)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl tini tzdata \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server/requirements.txt ./
# rawpy gives fast previews from RAW files; if no wheel exists for this CPU, exiftool is used instead
RUN pip install --no-cache-dir fastapi uvicorn pillow lxml numpy \
 && (pip install --no-cache-dir rawpy || echo "rawpy unavailable on this platform, using exiftool for previews")
COPY server/lpl ./lpl
COPY --from=web /web/dist ./web_dist
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/auth')" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "lpl.main"]
