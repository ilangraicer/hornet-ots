# hornet-ots — fork of OpenTAKServer for Project Hornet

Backend for the Hornet wildlife-tracker web app (LoRa/Meshtastic rhino collars).
Fork of [brian7704/OpenTAKServer](https://github.com/brian7704/OpenTAKServer); `origin` = ilangraicer/hornet-ots, `upstream` = brian7704.

**Master plan:** `~/Desktop/hornet/01 Tracker/Firmware & Software/Web App Plan.md`
**Security plan (deadline Nov 2026):** `.../Web App Security Plan.md` — until hardening, keep everything LAN/localhost-bound.

## Project decisions (do not re-litigate without Ilan)

- Web app is the **only** client — no iTAK/ATAK. EUD/Marti/cert-enrollment code will be stripped; TCP CoT :8088 stays **localhost-only** as ingest for the tracker bridge (`~/Desktop/hornet/code/tracker/gateway/bridge.py`).
- KEEP: core API+auth, CoT parser, position storage, Meshtastic module (MQTT, 2-way chat), socket.io push, user admin.
- STRIP (config-flag first, delete second, one feature per commit): video/MediaMTX, ADS-B, AIS, Mumble, plugin update server, device profiles, data packages, Mission API, CloudTAK, LDAP, groups, cert enrollment, email registration, TAK.gov linking.
- ADD via a Flask-blueprint plugin (`hornet-plugin`, see docs.opentakserver.io/plugins.html), not core edits: animal registry (node→animal), battery alerts, Traccar proxy (history/geofences).

## Dev environment (Mac, working as of Jul 11 2026)

```bash
brew services start rabbitmq postgresql@17      # deps (no Docker on this Mac)
PATH="/usr/bin:$PATH" .venv/bin/opentakserver   # run server; logs → ots.log
curl http://127.0.0.1:8081/api/health           # → {"status":"healthy"}
```

- venv: `.venv` (Python 3.12 via brew; system 3.14 breaks deps). Installed with `pip install -e .` + `pip install "psycopg[binary]"`.
- DB: Postgres `ots`/`hornet-dev`@localhost (OTS 1.7 requires Postgres; SQLite fails on CREATE SEQUENCE).
- Config: `~/ots/config.yml` (SQLALCHEMY_DATABASE_URI already set).
- **PATH override is mandatory**: Homebrew OpenSSL 3.5/3.6 `-passin` is broken on this Mac; LibreSSL (/usr/bin) works. Our first fork commit fixed `use_legacy` detection in `opentakserver/certificate_authority.py` (`_openssl_supports_legacy`).
- Web login (dev only): `administrator`/`password` at the UI (hornet-ui repo, vite :5173).

## Roadmap position

Milestone 1 (run unmodified) ✅ → **Milestone 2: chat spike** (web ↔ Meshtastic text via OTS Meshtastic MQTT module — riskiest feature) → 3: strip → 4: hornet features → 5: deploy to reserve Pi → 6: security (Nov 2026).

## Conventions

- Remote git ops (push/pull/fork) run on the Mac (Desktop Commander in Cowork; plain git in CC CLI) — the Cowork sandbox cannot reach GitHub.
- Keep upstream mergeable: prefer config-gating and isolated commits over sweeping rewrites.
