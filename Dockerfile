# NOTE: written but NOT built or run in the authoring environment (no Docker available there). Build it in CI before relying on it.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app/src
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --create-home aegis
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src ./src
COPY data/transcriptions ./data/transcriptions
COPY eval/golden_traces.json ./eval/golden_traces.json
RUN mkdir -p /app/data && chown -R aegis:aegis /app/data
USER aegis
ENV AEGIS_DB=/app/data/aegis.db AEGIS_SIDECARS=/app/data/transcriptions AEGIS_LLM_CACHE=/app/data/llm_cache.sqlite
EXPOSE 8000
# One worker per container: the rate limiter and trace cache are per-process (scale by replicas behind a limiter-aware proxy).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4).status==200 else 1)"
CMD ["python", "-m", "uvicorn", "aegis.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
