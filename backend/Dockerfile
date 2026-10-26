FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock && useradd --create-home --uid 10001 teamwiki
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh
USER teamwiki
EXPOSE 8000
CMD ["/app/entrypoint.sh"]
