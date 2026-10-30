# Hornet fork of OpenTAKServer — built from this repo's source (upstream's Dockerfile installs from GitHub master).

# --- build stage: compilers only live here (unishox2-py3 needs gcc) ---
FROM python:3.13-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY . /src
# .git is not in the build context, so pin the version for poetry-dynamic-versioning
ARG OTS_VERSION=1.7.14
ENV POETRY_DYNAMIC_VERSIONING_BYPASS=${OTS_VERSION}
RUN python -m venv /app/venv \
 && /app/venv/bin/pip install --no-cache-dir /src "psycopg[binary]"

# --- runtime stage ---
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1024 ots \
 && useradd --uid 1024 --gid 1024 --home /app --create-home ots \
 && mkdir -p /data && chown ots:ots /data
COPY --from=build /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH" \
    OTS_DATA_FOLDER=/data \
    PYTHONUNBUFFERED=1
USER ots
WORKDIR /app
VOLUME /data
EXPOSE 8081 8088

# One image, three processes (compose overrides the command): opentakserver | cot_parser | eud_handler
CMD ["opentakserver"]
STOPSIGNAL SIGINT
