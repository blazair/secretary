"""
app.py
======
The Flask application. Routes and wiring only; the thinking lives below in
repo.py and the pure planning modules.

The frontend is served from this same app, so requests are same-origin and the
signed session cookie is the whole authentication story. No CORS, no token.
"""

from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask, jsonify, request, send_from_directory

import api
import auth
import db
from config import (
    BEHIND_HTTPS,
    FRONTEND_DIR,
    PORT,
    SESSION_LIFETIME_DAYS,
    load_secret_key,
)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

    app.secret_key = load_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=BEHIND_HTTPS,
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_LIFETIME_DAYS),
        MAX_CONTENT_LENGTH=1024 * 1024,
        JSON_SORT_KEYS=False,
    )

    db.register(app)
    app.register_blueprint(auth.blueprint)
    app.register_blueprint(api.blueprint)

    _register_guards(app)
    _register_frontend(app)
    return app


def _register_guards(app: Flask) -> None:
    mutating_methods = {"POST", "PATCH", "PUT", "DELETE"}

    @app.before_request
    def require_json_on_mutations():
        """Cheap CSRF defence.

        SameSite=Lax already blocks cross-site form posts. Requiring a JSON
        content type closes the remaining gap, because an HTML form cannot set
        one without a preflight and there is no CORS here to allow it.
        """
        if request.method not in mutating_methods:
            return None
        if not request.path.startswith("/api/"):
            return None
        if not request.is_json:
            return jsonify({"error": "Requests must be sent as JSON."}), 415
        return None

    @app.after_request
    def no_cache_api(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.errorhandler(404)
    def not_found(error):
        """API paths stay JSON; everything else falls through to the frontend."""
        if request.path.startswith("/api/"):
            return jsonify({"error": "No such endpoint."}), 404
        return _serve_frontend(request.path.lstrip("/"))

    @app.errorhandler(500)
    def server_error(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Something broke on the server."}), 500
        return "Something broke on the server.", 500


def _serve_frontend(relative_path: str):
    if relative_path:
        candidate = FRONTEND_DIR / relative_path
        if candidate.is_file():
            return send_from_directory(FRONTEND_DIR, relative_path)
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({"error": "The frontend has not been built yet."}), 404


def _register_frontend(app: Flask) -> None:
    @app.get("/")
    def index():
        return _serve_frontend("")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "app": "secretary"})


app = create_app()


if __name__ == "__main__":
    db.init_schema()
    db.ensure_schema()
    use_waitress = os.environ.get("SECRETARY_DEV") != "1"
    if use_waitress:
        from waitress import serve

        print(f"Secretary on http://localhost:{PORT}")
        serve(app, host="0.0.0.0", port=PORT, threads=8)
    else:
        app.run(host="0.0.0.0", port=PORT, debug=True)
