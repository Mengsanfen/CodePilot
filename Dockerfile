FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml constraints.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 60 --retries 8 --resume-retries 8 -c constraints.txt .
COPY agent ./agent
COPY scripts ./scripts
COPY tests ./tests
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CODEPILOT_LOAD_ENV=false
CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
