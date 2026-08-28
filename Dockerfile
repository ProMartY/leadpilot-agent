FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./app ./app

RUN uv sync --frozen

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn app.web_demo:app --host 0.0.0.0 --port ${PORT:-8080}"]