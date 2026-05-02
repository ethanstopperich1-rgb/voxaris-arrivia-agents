FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for audio (Silero VAD pulls torch which needs libgomp)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY voxaris_agent/ ./voxaris_agent/

RUN pip install -e .

# Pre-download Silero weights at build time so cold start doesn't hit
# the network. (The plugin caches to ~/.cache/huggingface by default.)
RUN python -c "from livekit.plugins import silero; silero.VAD.load()"

CMD ["python", "-m", "voxaris_agent.worker", "start"]
