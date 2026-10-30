# Hornet fork of OpenTAKServer — built from this repo's source (upstream's Dockerfile installs from GitHub master).
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1024 ots \
 && useradd --uid 1024 --gid 1024 --home /app --create-home ots

WORKDIR /app
COPY --chown=ots:ots . /app/src
RUN python -m venv /app/venv \
 && /app/venv/bin/pip install --no-cache-dir /app/src "psycopg[binary]"

ENV PATH="/app/venv/bin:$PATH" \
    OTS_DATA_FOLDER=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown ots:ots /data
USER ots
VOLUME /data
EXPOSE 8081 8088

# One image, three processes (compose overrides the command): opentakserver | cot_parser | eud_handler
CMD ["opentakserver"]
STOPSIGNAL SIGINT
