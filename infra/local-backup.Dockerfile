# Local restore verification only. Never deployed to Heroku.
ARG PG_MAJOR=18
FROM postgres:${PG_MAJOR}-bookworm
ARG PG_MAJOR
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-${PG_MAJOR}-pg-stat-kcache && rm -rf /var/lib/apt/lists/*
