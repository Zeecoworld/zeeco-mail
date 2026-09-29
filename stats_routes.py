"""
Open / not-open tracking straight from Brevo. No database.

Brevo keeps your transactional events (up to 90 days), so we just query
GET /v3/smtp/statistics/events on demand and compare the lists.

Hook it into app.py with two lines:
    from stats_routes import stats_bp
    app.register_blueprint(stats_bp)
"""
import os
import hmac

import requests
from flask import Blueprint, jsonify, request, send_from_directory

stats_bp = Blueprint("stats", __name__)

EVENTS_URL = "https://api.brevo.com/v3/smtp/statistics/events"
PAGE_SIZE = 2500
MAX_DAYS = 90  # Brevo: date range cannot exceed 90 days


def _authorized():
    """If ADMIN_TOKEN is set, require it (recipient emails are private data)."""
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        return True
    supplied = request.headers.get("X-Admin-Token", "")
    return hmac.compare_digest(supplied, expected)


def _fetch_events(api_key, event, days, tag=None):
    """Fetch every page of one event type."""
    events, offset = [], 0
    while True:
        params = {"event": event, "days": days, "limit": PAGE_SIZE, "offset": offset}
        if tag:
            params["tags"] = tag
        r = requests.get(
            EVENTS_URL,
            headers={"api-key": api_key, "accept": "application/json"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        page = r.json().get("events", [])
        events.extend(page)
        if len(page) < PAGE_SIZE:
            return events
        offset += PAGE_SIZE


def _emails(events):
    return {(e.get("email") or "").lower() for e in events if e.get("email")}


@stats_bp.route("/stats")
def stats_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "stats.html")


@stats_bp.route("/stats/data")
def stats_data():
    if not _authorized():
        return jsonify({"error": "Wrong or missing admin token."}), 401

    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        return jsonify({"error": "BREVO_API_KEY is not set."}), 500

    try:
        days = max(1, min(int(request.args.get("days", 7)), MAX_DAYS))
    except ValueError:
        return jsonify({"error": "days must be a number."}), 400
    tag = (request.args.get("tag") or "").strip() or None

    try:
        delivered_ev = _fetch_events(api_key, "delivered", days, tag)
        opened_ev = _fetch_events(api_key, "opened", days, tag)
        clicked_ev = _fetch_events(api_key, "clicks", days, tag)
        proxy_ev = _fetch_events(api_key, "loadedByProxy", days, tag)
        hard_ev = _fetch_events(api_key, "hardBounces", days, tag)
        soft_ev = _fetch_events(api_key, "softBounces", days, tag)
    except requests.HTTPError as e:
        return jsonify({"error": "Brevo API error", "details": e.response.text}), e.response.status_code
    except requests.RequestException as e:
        return jsonify({"error": "Could not reach Brevo", "details": str(e)}), 502

    delivered = _emails(delivered_ev)
    opened = _emails(opened_ev)
    clicked = _emails(clicked_ev)
    # Apple Mail Privacy Protection etc. preload the tracking image through a proxy.
    proxy_only = _emails(proxy_ev) - opened
    bounced = _emails(hard_ev) | _emails(soft_ev)

    # A click proves the email was read even if the open pixel was blocked.
    confirmed = opened | clicked
    not_opened = delivered - confirmed - proxy_only

    # Batch tags seen in the data, so the page can offer a filter.
    tags = sorted({e.get("tag") for e in delivered_ev if e.get("tag")}, reverse=True)

    return jsonify({
        "days": days,
        "tag": tag,
        "tags": tags,
        "counts": {
            "delivered": len(delivered),
            "opened": len(confirmed),
            "not_opened": len(not_opened),
            "maybe_opened": len(proxy_only),
            "clicked": len(clicked),
            "bounced": len(bounced),
        },
        "opened": sorted(confirmed),
        "not_opened": sorted(not_opened),
        "maybe_opened": sorted(proxy_only),
        "clicked": sorted(clicked),
        "bounced": sorted(bounced),
    })
