"""Hornet-specific API for the hornet-web client.

Kept in its own blueprint so upstream OpenTAKServer merges stay simple.
"""

import datetime

from flask import Blueprint, jsonify, request
from flask_security import auth_required
from sqlalchemy import text

from opentakserver.extensions import db
from opentakserver.functions import iso8601_string_from_datetime

hornet_api_blueprint = Blueprint("hornet_api_blueprint", __name__)

MAX_HISTORY_POINTS = 5000


def _point_json(row) -> dict:
    return {
        "latitude": row.latitude,
        "longitude": row.longitude,
        "hae": row.hae,
        "speed": row.speed,
        "course": row.course,
        "battery": row.battery,
        "timestamp": iso8601_string_from_datetime(row.timestamp) if row.timestamp else None,
    }


@hornet_api_blueprint.route("/api/hornet/trackers")
@auth_required()
def list_trackers():
    """Every device that has reported a position, with its latest point."""
    rows = db.session.execute(
        text(
            """
            SELECT e.uid, e.callsign, e.last_event_time,
                   p.latitude, p.longitude, p.hae, p.speed, p.course, p.battery, p.timestamp
            FROM euds e
            LEFT JOIN LATERAL (
                SELECT latitude, longitude, hae, speed, course, battery, timestamp
                FROM points
                WHERE points.device_uid = e.uid AND latitude IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
            ) p ON TRUE
            ORDER BY e.callsign
            """
        )
    ).all()

    trackers = []
    for r in rows:
        trackers.append(
            {
                "uid": r.uid,
                "callsign": r.callsign,
                "last_event_time": (
                    iso8601_string_from_datetime(r.last_event_time) if r.last_event_time else None
                ),
                "last_point": _point_json(r) if r.latitude is not None else None,
            }
        )
    return jsonify({"trackers": trackers})


def _parse_time(value: str | None):
    if not value:
        return None
    try:
        t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    # points.timestamp is stored as naive UTC
    return t.astimezone(datetime.timezone.utc).replace(tzinfo=None) if t.tzinfo else t


@hornet_api_blueprint.route("/api/hornet/trackers/<uid>/history")
@auth_required()
def tracker_history(uid: str):
    """Positions for one device, oldest first. ?from=ISO&to=ISO (default: last 24 h)."""
    start = _parse_time(request.args.get("from"))
    end = _parse_time(request.args.get("to"))
    if start is False or end is False:
        return jsonify({"success": False, "error": "from/to must be ISO 8601"}), 400
    if end is None:
        end = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if start is None:
        start = end - datetime.timedelta(hours=24)

    rows = db.session.execute(
        text(
            """
            SELECT latitude, longitude, hae, speed, course, battery, timestamp
            FROM points
            WHERE device_uid = :uid AND latitude IS NOT NULL
              AND timestamp BETWEEN :start AND :end
            ORDER BY timestamp ASC
            LIMIT :limit
            """
        ),
        {"uid": uid, "start": start, "end": end, "limit": MAX_HISTORY_POINTS},
    ).all()

    return jsonify(
        {
            "uid": uid,
            "from": iso8601_string_from_datetime(start),
            "to": iso8601_string_from_datetime(end),
            "truncated": len(rows) == MAX_HISTORY_POINTS,
            "points": [_point_json(r) for r in rows],
        }
    )
