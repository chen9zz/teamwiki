FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
RUN pip install -r requirements.lock && pip install --no-deps . && useradd --create-home --uid 10001 teamwiki
COPY alembic.ini ./
COPY migrations ./migrations
USER teamwiki
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

