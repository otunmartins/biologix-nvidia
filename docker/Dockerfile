# The Python API. One long-running container, not a serverless function —
# see docs/DEPLOYMENT.md for why that is not a preference.
#
# Build from the repository root, not from this directory:
#     docker build -f docker/Dockerfile -t biologix-api .
FROM python:3.13-slim AS base

# PYTHONUNBUFFERED so logs appear in the platform's log viewer as they happen
# rather than when a buffer fills; PYTHONDONTWRITEBYTECODE because a container
# layer is read-only in spirit and .pyc files only add size.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# RDKit, numpy, scipy, scikit-learn and pandas all publish manylinux wheels,
# so there is no compiler in this image and no need for one. If a future
# dependency has no wheel, prefer finding one over adding build-essential:
# that choice is the difference between a 1 GB image and a 3 GB image.
COPY pyproject.toml README.md ./
COPY backend ./backend

RUN pip install --no-cache-dir ".[postgres]"

# The schema belongs to Alembic, and the container needs these to run it as a
# release step.
COPY alembic.ini ./
COPY migrations ./migrations

# Nothing here needs root. The data directory is writable because the cache
# and any SQLite fallback live under it.
RUN useradd --create-home --uid 10001 biologix \
    && mkdir -p /app/var \
    && chown -R biologix:biologix /app/var
USER biologix

ENV BIOLOGIX_DATA_DIR=/app/var \
    BIOLOGIX_CACHE_DIR=/app/var/cache \
    BIOLOGIX_HOST=0.0.0.0 \
    BIOLOGIX_ENV=production

EXPOSE 8080

# A container platform tells the app which port to listen on, and it is not
# always 8080. Honour $PORT when it is set, which is what Render, Railway,
# Fly and Cloud Run all do.
#
# One worker, deliberately. The job queue is an in-process thread pool, so a
# second worker process would hold jobs the first cannot see, and a campaign
# started on one would be invisible to the other. Concurrency comes from
# threads inside this process; see docs/DEPLOYMENT.md.
CMD ["sh", "-c", "exec uvicorn biologix.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
