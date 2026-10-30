"""Hornet-specific API for the hornet-web client.

Trackers + history, users/roles, groups, group messaging and map markers.
Kept in its own blueprint (plus one migration) so upstream OpenTAKServer merges stay simple.

Roles (Flask-Security role names → UI names):
    administrator → Admin     users, groups, everything
    user          → Operator  map, trackers, markers, messaging in their groups, create groups
    viewer        → Viewer    read-only map, trackers, markers; reads messages in their groups
"""

import datetime
import re

import bleach
from flask import Blueprint, current_app, jsonify, request
from flask_security import auth_required, current_user, hash_password
from flask_socketio import disconnect, join_room
from sqlalchemy import text

from opentakserver.extensions import db, logger, socketio
from opentakserver.functions import iso8601_string_from_datetime

hornet_api_blueprint = Blueprint("hornet_api_blueprint", __name__)

NS = "/socket.io"
ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER = "administrator", "user", "viewer"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)
MAX_HISTORY_POINTS = 5000
MAX_MESSAGE_LEN = 4000
MARKER_KINDS = ("point", "camp", "water", "hazard", "vehicle", "carcass", "sighting", "gate")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

_viewer_role_ready = False


@hornet_api_blueprint.before_app_request
def _ensure_viewer_role():
    global _viewer_role_ready
    if not _viewer_role_ready:
        current_app.security.datastore.find_or_create_role(name=ROLE_VIEWER, permissions={"user-read"})
        db.session.commit()
        _viewer_role_ready = True


# ---------------------------------------------------------------- helpers


def _iso(t):
    return iso8601_string_from_datetime(t) if t else None


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


def _role_of(user) -> str:
    if user.has_role(ROLE_ADMIN):
        return ROLE_ADMIN
    if user.has_role(ROLE_OPERATOR):
        return ROLE_OPERATOR
    return ROLE_VIEWER


def _is_admin() -> bool:
    return current_user.has_role(ROLE_ADMIN)


def _can_write() -> bool:
    return _role_of(current_user) in (ROLE_ADMIN, ROLE_OPERATOR)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _clean(value, max_len: int) -> str:
    return bleach.clean(str(value or "")).strip()[:max_len]


def _member_ids(group_id: int) -> list[int]:
    rows = db.session.execute(
        text("SELECT user_id FROM hornet_group_members WHERE group_id = :g"), {"g": group_id}
    ).all()
    return [r.user_id for r in rows]


def _can_read_group(group_id: int) -> bool:
    return _is_admin() or current_user.id in _member_ids(group_id)


def _emit_to_users(event: str, payload: dict, user_ids):
    for uid in set(user_ids):
        socketio.emit(event, payload, namespace=NS, to=f"hornet:user:{uid}")
    socketio.emit(event, payload, namespace=NS, to="hornet:admins")


# ---------------------------------------------------------------- socket.io


@socketio.on("hornet_join", namespace=NS)
def hornet_join(_data=None):
    """Client calls this after connecting. Rooms are per user (messages) and global (markers)."""
    if not current_user.is_authenticated:
        disconnect()
        return
    join_room("hornet:all")
    join_room(f"hornet:user:{current_user.id}")
    if _is_admin():
        join_room("hornet:admins")
    return {"ok": True, "user_id": current_user.id, "role": _role_of(current_user)}


# ---------------------------------------------------------------- me


@hornet_api_blueprint.route("/api/hornet/me")
@auth_required()
def hornet_me():
    return jsonify({"id": current_user.id, "username": current_user.username, "role": _role_of(current_user)})


# ---------------------------------------------------------------- trackers


def _point_json(row) -> dict:
    return {
        "latitude": row.latitude,
        "longitude": row.longitude,
        "hae": row.hae,
        "speed": row.speed,
        "course": row.course,
        "battery": row.battery,
        "timestamp": _iso(row.timestamp),
    }


@hornet_api_blueprint.route("/api/hornet/trackers")
@auth_required()
def list_trackers():
    """Every device that has reported, with its latest position."""
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
    return jsonify(
        {
            "trackers": [
                {
                    "uid": r.uid,
                    "callsign": r.callsign,
                    "last_event_time": _iso(r.last_event_time),
                    "last_point": _point_json(r) if r.latitude is not None else None,
                }
                for r in rows
            ]
        }
    )


def _parse_time(value):
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
        return _err("from/to must be ISO 8601")
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
            "from": _iso(start),
            "to": _iso(end),
            "truncated": len(rows) == MAX_HISTORY_POINTS,
            "points": [_point_json(r) for r in rows],
        }
    )


# ---------------------------------------------------------------- users (admin)


def _user_json(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": _role_of(u),
        "active": bool(u.active),
        "last_login_at": _iso(u.last_login_at) if getattr(u, "last_login_at", None) else None,
    }


def _set_role(user, role: str):
    ds = current_app.security.datastore
    for r in ROLES:
        if user.has_role(r) and r != role:
            ds.remove_role_from_user(user, r)
    if not user.has_role(role):
        ds.add_role_to_user(user, role)


@hornet_api_blueprint.route("/api/hornet/users")
@auth_required()
def list_users():
    """Admins get the full list; everyone else gets id + username (for picking group members)."""
    model = current_app.security.datastore.user_model
    users = db.session.query(model).order_by(model.username).all()
    if _is_admin():
        return jsonify({"users": [_user_json(u) for u in users]})
    return jsonify({"users": [{"id": u.id, "username": u.username} for u in users if u.active]})


@hornet_api_blueprint.route("/api/hornet/users", methods=["POST"])
@auth_required()
def create_user():
    if not _is_admin():
        return _err("Admins only", 403)
    data = _json_body()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    role = data.get("role") or ROLE_OPERATOR
    if not USERNAME.match(username):
        return _err("Username: 3-32 characters, letters, digits, . _ -")
    if len(password) < 12:
        return _err("Password must be at least 12 characters")
    if role not in ROLES:
        return _err("Unknown role")
    ds = current_app.security.datastore
    if ds.find_user(username=username):
        return _err("That username is taken")
    user = ds.create_user(username=username, password=hash_password(password), roles=[role])
    db.session.commit()
    logger.info(f"hornet: {current_user.username} created user {username} ({role})")
    return jsonify({"success": True, "user": _user_json(user)})


@hornet_api_blueprint.route("/api/hornet/users/<int:user_id>", methods=["PATCH"])
@auth_required()
def update_user(user_id: int):
    if not _is_admin():
        return _err("Admins only", 403)
    ds = current_app.security.datastore
    user = ds.find_user(id=user_id)
    if not user:
        return _err("No such user", 404)
    data = _json_body()
    is_self = user.id == current_user.id

    if "role" in data:
        if data["role"] not in ROLES:
            return _err("Unknown role")
        if is_self and data["role"] != ROLE_ADMIN:
            return _err("You can't remove your own admin role")
        _set_role(user, data["role"])
    if "active" in data:
        if is_self and not data["active"]:
            return _err("You can't deactivate yourself")
        (ds.activate_user if data["active"] else ds.deactivate_user)(user)
    if data.get("password"):
        if len(str(data["password"])) < 12:
            return _err("Password must be at least 12 characters")
        user.password = hash_password(str(data["password"]))
    db.session.commit()
    logger.info(f"hornet: {current_user.username} updated user {user.username}: {sorted(k for k in data if k != 'password')}")
    return jsonify({"success": True, "user": _user_json(user)})


# ---------------------------------------------------------------- groups


def _groups_json(where: str = "", params: dict | None = None) -> list[dict]:
    rows = db.session.execute(
        text(
            f"""
            SELECT g.id, g.name, g.description, g.created_by, g.created_at,
                   (SELECT max(id) FROM hornet_messages m WHERE m.group_id = g.id) AS last_message_id,
                   COALESCE(json_agg(json_build_object('id', u.id, 'username', u.username) ORDER BY u.username)
                            FILTER (WHERE u.id IS NOT NULL), '[]') AS members
            FROM hornet_groups g
            LEFT JOIN hornet_group_members gm ON gm.group_id = g.id
            LEFT JOIN "user" u ON u.id = gm.user_id
            {where}
            GROUP BY g.id
            ORDER BY g.name
            """
        ),
        params or {},
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "created_by": r.created_by,
            "created_at": _iso(r.created_at),
            "last_message_id": r.last_message_id,
            "members": r.members,
        }
        for r in rows
    ]


def _group_or_404(group_id: int):
    return db.session.execute(text("SELECT * FROM hornet_groups WHERE id = :g"), {"g": group_id}).first()


def _set_members(group_id: int, member_ids) -> list[int]:
    valid = {
        r.id
        for r in db.session.execute(
            text('SELECT id FROM "user" WHERE id = ANY(:ids)'), {"ids": [int(i) for i in member_ids]}
        ).all()
    }
    db.session.execute(text("DELETE FROM hornet_group_members WHERE group_id = :g"), {"g": group_id})
    for uid in valid:
        db.session.execute(
            text("INSERT INTO hornet_group_members (group_id, user_id) VALUES (:g, :u)"), {"g": group_id, "u": uid}
        )
    return sorted(valid)


@hornet_api_blueprint.route("/api/hornet/groups")
@auth_required()
def list_groups():
    if _is_admin():
        return jsonify({"groups": _groups_json()})
    return jsonify(
        {
            "groups": _groups_json(
                "WHERE g.id IN (SELECT group_id FROM hornet_group_members WHERE user_id = :me)",
                {"me": current_user.id},
            )
        }
    )


@hornet_api_blueprint.route("/api/hornet/groups", methods=["POST"])
@auth_required()
def create_group():
    if not _can_write():
        return _err("Viewers can't create groups", 403)
    data = _json_body()
    name = _clean(data.get("name"), 100)
    if len(name) < 2:
        return _err("Group name is too short")
    if db.session.execute(text("SELECT 1 FROM hornet_groups WHERE lower(name) = lower(:n)"), {"n": name}).first():
        return _err("A group with that name already exists")
    gid = db.session.execute(
        text("INSERT INTO hornet_groups (name, description, created_by) VALUES (:n, :d, :u) RETURNING id"),
        {"n": name, "d": _clean(data.get("description"), 500) or None, "u": current_user.id},
    ).scalar_one()
    members = _set_members(gid, list(data.get("member_ids") or []) + [current_user.id])
    db.session.commit()
    group = _groups_json("WHERE g.id = :g", {"g": gid})[0]
    _emit_to_users("hornet_groups_changed", {"group_id": gid}, members)
    return jsonify({"success": True, "group": group})


@hornet_api_blueprint.route("/api/hornet/groups/<int:group_id>", methods=["PATCH"])
@auth_required()
def update_group(group_id: int):
    g = _group_or_404(group_id)
    if not g:
        return _err("No such group", 404)
    if not (_is_admin() or (_can_write() and g.created_by == current_user.id)):
        return _err("Only the group's creator or an admin can change it", 403)
    data = _json_body()
    before = _member_ids(group_id)
    if "name" in data:
        name = _clean(data["name"], 100)
        if len(name) < 2:
            return _err("Group name is too short")
        clash = db.session.execute(
            text("SELECT 1 FROM hornet_groups WHERE lower(name) = lower(:n) AND id <> :g"), {"n": name, "g": group_id}
        ).first()
        if clash:
            return _err("A group with that name already exists")
        db.session.execute(text("UPDATE hornet_groups SET name = :n WHERE id = :g"), {"n": name, "g": group_id})
    if "description" in data:
        db.session.execute(
            text("UPDATE hornet_groups SET description = :d WHERE id = :g"),
            {"d": _clean(data["description"], 500) or None, "g": group_id},
        )
    after = before
    if "member_ids" in data:
        after = _set_members(group_id, data["member_ids"] or [])
    db.session.commit()
    _emit_to_users("hornet_groups_changed", {"group_id": group_id}, set(before) | set(after))
    return jsonify({"success": True, "group": _groups_json("WHERE g.id = :g", {"g": group_id})[0]})


@hornet_api_blueprint.route("/api/hornet/groups/<int:group_id>", methods=["DELETE"])
@auth_required()
def delete_group(group_id: int):
    g = _group_or_404(group_id)
    if not g:
        return _err("No such group", 404)
    if not (_is_admin() or (_can_write() and g.created_by == current_user.id)):
        return _err("Only the group's creator or an admin can delete it", 403)
    members = _member_ids(group_id)
    db.session.execute(text("DELETE FROM hornet_groups WHERE id = :g"), {"g": group_id})
    db.session.commit()
    _emit_to_users("hornet_groups_changed", {"group_id": group_id, "deleted": True}, members)
    return jsonify({"success": True})


# ---------------------------------------------------------------- messages


def _message_json(r) -> dict:
    return {
        "id": r.id,
        "group_id": r.group_id,
        "sender_id": r.sender_id,
        "sender": r.sender,
        "body": r.body,
        "created_at": _iso(r.created_at),
    }


@hornet_api_blueprint.route("/api/hornet/groups/<int:group_id>/messages")
@auth_required()
def list_messages(group_id: int):
    """Newest last. ?before=<message id> pages backwards; ?limit (max 200)."""
    if not _group_or_404(group_id):
        return _err("No such group", 404)
    if not _can_read_group(group_id):
        return _err("You're not in this group", 403)
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    before = request.args.get("before", type=int)
    rows = db.session.execute(
        text(
            f"""
            SELECT m.id, m.group_id, m.sender_id, u.username AS sender, m.body, m.created_at
            FROM hornet_messages m LEFT JOIN "user" u ON u.id = m.sender_id
            WHERE m.group_id = :g {"AND m.id < :before" if before else ""}
            ORDER BY m.id DESC LIMIT :limit
            """
        ),
        {"g": group_id, "before": before, "limit": limit},
    ).all()
    return jsonify({"messages": [_message_json(r) for r in reversed(rows)], "has_more": len(rows) == limit})


@hornet_api_blueprint.route("/api/hornet/groups/<int:group_id>/messages", methods=["POST"])
@auth_required()
def send_message(group_id: int):
    if not _group_or_404(group_id):
        return _err("No such group", 404)
    members = _member_ids(group_id)
    if current_user.id not in members:
        return _err("Join the group to send messages", 403)
    if not _can_write():
        return _err("Viewers can't send messages", 403)
    body = str(_json_body().get("body") or "").strip()
    if not body:
        return _err("Message is empty")
    if len(body) > MAX_MESSAGE_LEN:
        return _err(f"Message is longer than {MAX_MESSAGE_LEN} characters")
    # Stored as plain text; the client renders it as text (never HTML)
    r = db.session.execute(
        text(
            """
            INSERT INTO hornet_messages (group_id, sender_id, body) VALUES (:g, :u, :b)
            RETURNING id, group_id, sender_id, :name AS sender, body, created_at
            """
        ),
        {"g": group_id, "u": current_user.id, "b": body, "name": current_user.username},
    ).first()
    db.session.commit()
    msg = _message_json(r)
    _emit_to_users("hornet_message", msg, members)
    return jsonify({"success": True, "message": msg})


# ---------------------------------------------------------------- markers


def _marker_json(r) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "color": r.color,
        "latitude": r.latitude,
        "longitude": r.longitude,
        "note": r.note,
        "created_by": r.created_by,
        "created_by_name": r.created_by_name,
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


_MARKER_SELECT = """
    SELECT m.*, u.username AS created_by_name
    FROM hornet_markers m LEFT JOIN "user" u ON u.id = m.created_by
"""


def _marker_fields(data: dict, partial: bool):
    out = {}
    if "name" in data or not partial:
        name = _clean(data.get("name"), 120)
        if not name:
            return None, "Marker needs a name"
        out["name"] = name
    if "kind" in data or not partial:
        kind = data.get("kind") or "point"
        if kind not in MARKER_KINDS:
            return None, "Unknown marker type"
        out["kind"] = kind
    if "color" in data or not partial:
        color = data.get("color") or "#e0b341"
        if not HEX_COLOR.match(color):
            return None, "Colour must be #rrggbb"
        out["color"] = color
    for k, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180)):
        if k in data or not partial:
            try:
                v = float(data.get(k))
            except (TypeError, ValueError):
                return None, f"{k} is missing"
            if not lo <= v <= hi:
                return None, f"{k} out of range"
            out[k] = v
    if "note" in data:
        out["note"] = _clean(data.get("note"), 2000) or None
    return out, None


@hornet_api_blueprint.route("/api/hornet/markers")
@auth_required()
def list_markers():
    rows = db.session.execute(text(_MARKER_SELECT + " ORDER BY m.id")).all()
    return jsonify({"markers": [_marker_json(r) for r in rows]})


@hornet_api_blueprint.route("/api/hornet/markers", methods=["POST"])
@auth_required()
def create_marker():
    if not _can_write():
        return _err("Viewers can't add markers", 403)
    fields, error = _marker_fields(_json_body(), partial=False)
    if error:
        return _err(error)
    fields.setdefault("note", None)
    mid = db.session.execute(
        text(
            """
            INSERT INTO hornet_markers (name, kind, color, latitude, longitude, note, created_by)
            VALUES (:name, :kind, :color, :latitude, :longitude, :note, :u) RETURNING id
            """
        ),
        {**fields, "u": current_user.id},
    ).scalar_one()
    db.session.commit()
    marker = _marker_json(db.session.execute(text(_MARKER_SELECT + " WHERE m.id = :id"), {"id": mid}).first())
    socketio.emit("hornet_marker", {"action": "upsert", "marker": marker}, namespace=NS, to="hornet:all")
    return jsonify({"success": True, "marker": marker})


def _marker_editable(r) -> bool:
    return _is_admin() or (_can_write() and r.created_by == current_user.id)


@hornet_api_blueprint.route("/api/hornet/markers/<int:marker_id>", methods=["PATCH"])
@auth_required()
def update_marker(marker_id: int):
    r = db.session.execute(text("SELECT * FROM hornet_markers WHERE id = :id"), {"id": marker_id}).first()
    if not r:
        return _err("No such marker", 404)
    if not _marker_editable(r):
        return _err("Only the marker's creator or an admin can change it", 403)
    fields, error = _marker_fields(_json_body(), partial=True)
    if error:
        return _err(error)
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys come from _marker_fields, not the client
        db.session.execute(
            text(f"UPDATE hornet_markers SET {sets}, updated_at = now() WHERE id = :id"), {**fields, "id": marker_id}
        )
        db.session.commit()
    marker = _marker_json(db.session.execute(text(_MARKER_SELECT + " WHERE m.id = :id"), {"id": marker_id}).first())
    socketio.emit("hornet_marker", {"action": "upsert", "marker": marker}, namespace=NS, to="hornet:all")
    return jsonify({"success": True, "marker": marker})


@hornet_api_blueprint.route("/api/hornet/markers/<int:marker_id>", methods=["DELETE"])
@auth_required()
def delete_marker(marker_id: int):
    r = db.session.execute(text("SELECT * FROM hornet_markers WHERE id = :id"), {"id": marker_id}).first()
    if not r:
        return _err("No such marker", 404)
    if not _marker_editable(r):
        return _err("Only the marker's creator or an admin can delete it", 403)
    db.session.execute(text("DELETE FROM hornet_markers WHERE id = :id"), {"id": marker_id})
    db.session.commit()
    socketio.emit("hornet_marker", {"action": "delete", "id": marker_id}, namespace=NS, to="hornet:all")
    return jsonify({"success": True})
