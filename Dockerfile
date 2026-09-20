# tesla-model-extractor with GDRE Tools bundled.
#   docker run --rm -v "$PWD":/work ghcr.io/koenhendriks/tesla-model-extractor unreal /work/Tesla_4.60.5.apkm --all -o /work/unreal
#   docker build -t tesla-model-extractor .
#   docker run --rm -v "$PWD":/work ghcr.io/koenhendriks/tesla-model-extractor /work/Tesla_4.60.0.apks --models bayberry -o /work/packs
FROM python:3.12-slim AS base

ARG GDRE_VERSION=2.6.4
ARG GDRE_SHA256=eda8cb09e64a060728fa371aa80ae148d3c5584a7de2f553699936daa84e7b4e

# GDRE Tools is a Godot 4 headless build: it still links X11/GL/ALSA/fontconfig even when run --headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip \
        libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxext6 libxrender1 \
        libgl1 libglu1-mesa libasound2 libpulse0 libfontconfig1 libfreetype6 libdbus-1-3 libudev1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/gdre && cd /opt/gdre \
    && curl -fsSL -o gdre.zip "https://github.com/GDRETools/gdsdecomp/releases/download/v${GDRE_VERSION}/GDRE_tools-v${GDRE_VERSION}-linux.zip" \
    && echo "${GDRE_SHA256}  gdre.zip" | sha256sum -c - \
    && unzip -q gdre.zip && rm gdre.zip && chmod +x /opt/gdre/gdre_tools.x86_64 \
    && (XDG_DATA_HOME=/tmp/xdg /opt/gdre/gdre_tools.x86_64 --headless --version 2>&1 | grep -q "Godot RE Tools")

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV GDRE_TOOLS=/opt/gdre/gdre_tools.x86_64 \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_DATA_HOME=/tmp/xdg \
    PYTHONUNBUFFERED=1
WORKDIR /work
ENTRYPOINT ["tesla-model-extract"]
CMD ["--help"]
