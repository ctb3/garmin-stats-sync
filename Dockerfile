FROM python:3.12-slim

# zoneinfo needs the system tz database for LOCAL_TZ to resolve.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY garmin_stats_sync ./garmin_stats_sync
COPY scripts ./scripts

RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

ENTRYPOINT ["garmin-stats-sync"]
CMD ["loop"]
