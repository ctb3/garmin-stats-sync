FROM python:3.12-slim

# zoneinfo needs the system tz database for LOCAL_TZ to resolve.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY garmin_stats_sync ./garmin_stats_sync

RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

# The ingest listener. Publish it to loopback only and let the reverse proxy
# terminate TLS in front of it - see the README.
EXPOSE 8080

ENTRYPOINT ["garmin-stats-sync"]
CMD ["loop"]
