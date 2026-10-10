FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS client
WORKDIR /build
COPY client/package*.json ./
RUN npm ci --ignore-scripts
COPY client/ ./
RUN npm test && npm run build

FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir /state && chown app:app /state
COPY requirements.txt server/requirements-db.txt ./
RUN pip install -r requirements.txt -r requirements-db.txt
COPY server/ ./server/
COPY --from=client /build/dist/ ./client/dist/
USER app
ENV VIPMBB_DATA_DIR=/state
CMD ["python", "-m", "uvicorn", "server.app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM runtime AS test
COPY tests/ ./tests/
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
