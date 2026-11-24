"""The web UI's server: Python's standard library only, bound to 127.0.0.1.

    GET  /                       the page (static/index.html, with this run's token filled in)
    GET  /vendor/<file>          xterm.js and its stylesheet, copied into the repository
    GET  /api/cost               every recorded LLM run (tokens, estimated dollars)  `jobfinder cost report`
    GET  /api/doc?id=&kind=&token=  one of a posting's documents (CV, cover letter, ...) from data/
    GET  /api/state              postings, settings, searches, profile status, terminal status
    POST /api/approve            {"ids": [...]}                     `jobfinder jobs approve`
    POST /api/interview          {"ids": [...], "back": false}      `jobfinder track interview`
    POST /api/delete             {"ids": [...]}                     marks postings deleted (hidden, never added again)
    POST /api/open-doc           {"id": "...", "kind": "cv"}         opens the file in this computer's own app
    POST /api/mark-applied       {"id": "...", "date": "today"}     `jobfinder apply mark`
    POST /api/settings/<stage>   fetch | score | cv | apply         the `... configure` commands
    POST /api/searches           {"searches": [...]}                data/searches/searches.json
    POST /api/source-check       verify APIFY_TOKEN                 `jobfinder source check`
    POST /api/cv-upload          {"name": "...", "data": base64}    saves a PDF as data/profile/cv.pdf
    POST /api/apify-token        {"token": "..."}                   writes APIFY_TOKEN to .env (never read back)
    POST /api/safety-model       download the local safety model    `jobfinder safety model`
    POST /api/term/start|input|resize|stop|close, GET /api/term/stream?id=    the terminal tabs (terminal.py)

Every action calls the function the matching command calls, so the UI has no rules of its own.
A posting the safety check blocked leaves here as an id and a stage only, like `jobs list`.

Who may call it: a request needs a `Host` of 127.0.0.1 or localhost on this port (so a page on
another site cannot reach it through DNS rebinding), a POST also an `Origin` of this page, and
every /api call this run's random token, which only the page we serve carries. The terminal
endpoints start Claude Code, so they are what these checks are for.
"""

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from jobfinder import apply, cost, guard, handoff, jobs, overview, safety, searches, send, track
from jobfinder.cv import settings as cv_settings
from jobfinder.fetch import settings as fetch_settings
from jobfinder.paths import (APPLY_SETTINGS_JSON, COST_USAGE_JSON, CV_SETTINGS_JSON, DATA_DIR, ENV_FILE, FETCH_SETTINGS_JSON, JOBS_JSON,
                             PROFILE_DIR, PROFILE_JSON, SAFETY_MODEL_DIR, SCORE_SETTINGS_JSON, SEARCHES_JSON)
from jobfinder.profile.gaps import missing
from jobfinder.profile.render import render_profile_md
from jobfinder.profile.schema import validate
from jobfinder.score import settings as score_settings
from jobfinder.sources import actors as actor_registry
from jobfinder.sources.apify import SourceError, check, make_client
from jobfinder.ui import terminal

STATIC = Path(__file__).parent / "static"
VENDOR = ("xterm.js", "xterm.css", "addon-fit.js")
MAX_BODY = 256 * 1024
CV_MAX = 8 * 1024 * 1024  # a CV PDF; its base64 body is a third bigger
CV_NAME = "cv.pdf"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{8,200}$")
DOC_TYPES = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8"}
SETTINGS_ERRORS = (score_settings.SettingsError, cv_settings.SettingsError, send.SendError, SourceError)
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Refused(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def http_url(url) -> str | None:
    """A posting's links come from the posting, so only http(s) ones go to the page."""
    return url if isinstance(url, str) and re.match(r"^https?://", url) else None


def profile_status() -> dict:
    if not PROFILE_JSON.exists():
        return {"exists": False}
    try:
        profile = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"exists": True, "valid": False, "errors": 1, "missing": 0}
    identity, prefs = profile.get("identity") or {}, profile.get("preferences") or {}
    place, errors = identity.get("location") or {}, validate(profile)
    return {"exists": True, "valid": not errors, "errors": len(errors),
            "missing": len(missing(profile)), "name": identity.get("full_name"),
            "roles": prefs.get("target_roles") or [],
            "summary": render_profile_md(profile),
            "location": ", ".join(x for x in (place.get("city"), place.get("country")) if x)}


def document_path(job: dict, kind) -> Path | None:
    """The file of one of a posting's documents (`apply.DOCUMENTS`), only when it is a regular file
    of a known type inside data/; None otherwise. A blocked posting has none."""
    relative = (job.get("documents") or {}).get(kind) if kind in apply.DOCUMENTS else None
    if not isinstance(relative, str) or (job.get("safety") or {}).get("verdict") not in ("pass", "warn"):
        return None
    root = DATA_DIR.resolve()
    path = (root / relative).resolve()
    if path.suffix.lower() not in DOC_TYPES or not path.is_file() or not path.is_relative_to(root):
        return None
    return path


def cv_files() -> list[str]:
    return sorted(p.name for p in PROFILE_DIR.glob("*.pdf")) if PROFILE_DIR.is_dir() else []


def safety_model_ready() -> bool:
    try:
        return (SAFETY_MODEL_DIR / safety.load_rules()["model"]["model_file"]).is_file()
    except (OSError, KeyError, ValueError):
        return False


def checks() -> dict:
    """What this computer has: the programs the buttons need."""
    return {"claude": shutil.which(terminal.CLAUDE) is not None, "node": shutil.which("npx") is not None,
            "safety_model": safety_model_ready()}


def upload_cv(body: dict) -> dict:
    """Save an uploaded PDF as data/profile/cv.pdf (the file /create-profile reads)."""
    data = body.get("data")
    if not isinstance(data, str):
        raise Refused(HTTPStatus.BAD_REQUEST, "data must be the file as base64 text")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise Refused(HTTPStatus.BAD_REQUEST, "The file could not be read.") from None
    if len(raw) > CV_MAX:
        raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The CV is larger than 8 MB.")
    if not raw.startswith(b"%PDF-"):
        raise Refused(HTTPStatus.BAD_REQUEST, "Only PDF files are accepted. Export your CV as a PDF.")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    target = PROFILE_DIR / CV_NAME
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(raw)
    tmp.replace(target)
    return {"saved": CV_NAME, "size": len(raw)}


def save_apify_token(body: dict) -> dict:
    """Write APIFY_TOKEN into .env (private to the owner). The value is never sent back to the page."""
    value = body.get("token")
    value = value.strip() if isinstance(value, str) else ""
    if not TOKEN_RE.match(value):
        raise Refused(HTTPStatus.BAD_REQUEST, "That does not look like an Apify token (letters, digits, - and _ only).")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not line.startswith("APIFY_TOKEN=")]
    lines.append(f"APIFY_TOKEN={value}")
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)  # an older file keeps its mode when opened
    os.environ["APIFY_TOKEN"] = value
    return {"token_set": True}


def install_safety_model() -> dict:
    spec = safety.load_rules()["model"]
    try:
        try:
            guard.verify(spec)
        except guard.GuardError:
            guard.download(spec)
    except Exception as exc:  # network, Hugging Face access, hash mismatch
        raise Refused(HTTPStatus.CONFLICT, f"Could not download the safety model: {type(exc).__name__}: {exc}") from None
    return {"safety_model": True}


def token_set() -> bool:
    load_dotenv(ENV_FILE)
    return bool(os.environ.get("APIFY_TOKEN", "").strip())


def settings_state() -> dict:
    try:
        apply_settings = send.load_settings(APPLY_SETTINGS_JSON)
    except send.SendError:
        apply_settings = dict(send.DEFAULTS)
    return {"fetch": fetch_settings.load(FETCH_SETTINGS_JSON), "score": score_settings.load(SCORE_SETTINGS_JSON),
            "cv": cv_settings.load(CV_SETTINGS_JSON), "apply": apply_settings}


def state(sessions: terminal.Sessions) -> dict:
    store = jobs.load(JOBS_JSON)
    by_id = {job["id"]: job for job in store["jobs"]}
    actors = actor_registry.load()
    labels = {actor["name"]: actor.get("label", actor["name"]) for actor in actors}
    rows = []
    for row in overview.listing(store)["jobs"]:
        if row["stage"] == "blocked":  # id and stage only: its text is never shown
            rows.append({"id": row["id"], "stage": "blocked"})
            continue
        job = by_id[row["id"]]
        rows.append({**{key: row[key] for key in ("id", "title", "company_name", "location", "stage", "score", "warnings",
                                                   "outcome")},
                     "url": http_url(row["url"]), "apply_url": http_url(row["apply_url"]),
                     "source": labels.get(job.get("data_source"), job.get("data_source")),
                     "data_source": job.get("data_source"),
                     "interview_round": (job.get("application") or {}).get("interview_round"),
                     "created_at": (job.get("created_at") or "")[:10] or None,
                     "applied_at": (row["applied_at"] or "")[:10] or None,
                     "documents": {kind: {"name": Path(name).name, "exists": document_path(job, kind) is not None}
                                   for kind, name in (job.get("documents") or {}).items()
                                   if kind in apply.DOCUMENTS and isinstance(name, str)}})
    try:
        search_data = searches.load(SEARCHES_JSON)["searches"]
    except FileNotFoundError:
        search_data = []
    try:
        sent_today = send.sent_on(store)
    except Exception:  # a record written by an older version must not blank the page
        sent_today = 0
    return {"updated_at": store.get("updated_at"), "jobs": rows, "settings": settings_state(), "searches": search_data,
            "sources": [{"name": a["name"], "label": a.get("label", a["name"]), "free": a.get("kind", "apify") != "apify"}
                        for a in actors],
            "profile": profile_status(), "cv_files": cv_files(), "checks": checks(), "apify_token": token_set(), "sent_today": sent_today,
            "terminal": sessions.status(), "commands": sorted(terminal.COMMANDS)}


def save_fetch(body: dict) -> dict:
    current = fetch_settings.load(FETCH_SETTINGS_JSON)
    names = body.get("data_sources")
    if names is not None:
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or not names:
            raise Refused(HTTPStatus.BAD_REQUEST, "Choose at least one source.")
        chosen = actor_registry.select(actor_registry.load(), names)
        every = len(chosen) == len(actor_registry.load())
        current["data_sources"] = ["all"] if every else [a["name"] for a in chosen]
    if body.get("date_posted") is not None:
        current["date_posted"] = body["date_posted"]
    if body.get("exclude_words") is not None:
        current["exclude_words"] = body["exclude_words"]
    try:
        return fetch_settings.save(current, FETCH_SETTINGS_JSON)
    except SourceError as exc:
        raise Refused(HTTPStatus.BAD_REQUEST, str(exc)) from None


def save_score(body: dict) -> dict:
    current = score_settings.load(SCORE_SETTINGS_JSON)
    current.update({key: body[key] for key in score_settings.DEFAULTS if key in body})
    return score_settings.save(current, SCORE_SETTINGS_JSON)


def save_cv(body: dict) -> dict:
    current = cv_settings.load(CV_SETTINGS_JSON)
    if "writer_model" in body:
        current["writer_model"] = body["writer_model"]
    if "review" in body:
        current["review"] = body["review"] is True
    if current["writer_model"] not in cv_settings.WRITER_MODELS:
        raise cv_settings.SettingsError(f"writer_model must be one of {', '.join(cv_settings.WRITER_MODELS)}")
    return cv_settings.save(current, CV_SETTINGS_JSON)


def save_apply(body: dict) -> dict:
    return send.save_settings({key: body[key] for key in send.DEFAULTS if key in body}, APPLY_SETTINGS_JSON)


SAVERS = {"fetch": save_fetch, "score": save_score, "cv": save_cv, "apply": save_apply}


def save_searches(body: dict) -> dict:
    rows = body.get("searches")
    if not isinstance(rows, list):
        raise Refused(HTTPStatus.BAD_REQUEST, "searches must be a list")
    seen, clean = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, "each search must be an object")
        search = {key: row[key] for key in ("id", "keywords", "location", "limit", "enabled") if key in row}
        if not isinstance(search.get("id"), str) or not search["id"]:
            base = re.sub(r"[^a-z0-9]+", "-", f"{search.get('keywords', '')} {search.get('location', '')}".lower()).strip("-")
            search["id"] = base[:60].strip("-") or "search"
        unique, n = search["id"], 2
        while unique in seen:
            unique, n = f"{search['id']}-{n}", n + 1
        search["id"] = unique
        seen.add(unique)
        clean.append(search)
    try:
        return searches.save({"searches": clean}, SEARCHES_JSON)
    except ValueError as exc:
        raise Refused(HTTPStatus.BAD_REQUEST, str(exc)) from None


def approve(body: dict) -> dict:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise Refused(HTTPStatus.BAD_REQUEST, "ids must be a list of posting ids")
    store = jobs.load(JOBS_JSON)
    result = jobs.approve(store, ids)
    if result["approved"]:
        jobs.save(store, JOBS_JSON)
    return result


def delete(body: dict) -> dict:
    """Mark postings `deleted`: they leave every list, and a later search does not add them again."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise Refused(HTTPStatus.BAD_REQUEST, "ids must be a list of posting ids")
    with handoff.jobs_lock(JOBS_JSON):
        store = jobs.load(JOBS_JSON)
        result = jobs.delete(store, ids)
        if result["deleted"]:
            jobs.save(store, JOBS_JSON)
    return result


def interview(body: dict) -> dict:
    """Move applications to the next interview round, or one back (`track.interview`)."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise Refused(HTTPStatus.BAD_REQUEST, "ids must be a list of posting ids")
    with handoff.jobs_lock(JOBS_JSON):
        store = jobs.load(JOBS_JSON)
        result = track.interview(store, ids, back=body.get("back") is True)
        if result["moved"]:
            jobs.save(store, JOBS_JSON)
    return result


def mark_applied(body: dict) -> dict:
    job_id, day = body.get("id"), body.get("date", "today")
    if not isinstance(job_id, str) or not isinstance(day, str):
        raise Refused(HTTPStatus.BAD_REQUEST, "id and date must be text")
    store = jobs.load(JOBS_JSON)
    try:
        when = apply.parse_day(day)
        marked = apply.mark_applied(apply.find(store, job_id), None if day == "today" else when, early=True)
    except apply.ApplyError as exc:
        raise Refused(HTTPStatus.CONFLICT, str(exc)) from None
    jobs.save(store, JOBS_JSON)
    return marked


def cost_state() -> dict:
    """The cost hook's records, read again from the transcripts still on disk, one row per run."""
    try:
        with cost._locked(COST_USAGE_JSON) as usage:
            cost.refresh(usage)
        store = jobs.load(JOBS_JSON) if JOBS_JSON.exists() else None
        runs = cost.detail(usage, store)
    except (cost.CostError, OSError, ValueError) as exc:
        raise Refused(HTTPStatus.CONFLICT, f"Could not read the cost records: {exc}") from None
    unknown = sorted({m for run in usage["runs"].values() for m in run.get("unknown_models", [])})
    return {"runs": runs, "unknown_models": unknown, "estimate": True}


def open_document(body: dict) -> dict:
    """Open a posting's document in the computer's own app (the server runs on the candidate's machine)."""
    job_id, kind = body.get("id"), body.get("kind")
    job = next((j for j in jobs.load(JOBS_JSON)["jobs"] if j["id"] == job_id), None) if isinstance(job_id, str) else None
    path = document_path(job, kind) if job else None
    if path is None:
        raise Refused(HTTPStatus.NOT_FOUND, "No such document.")
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            opener = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
            if opener is None:
                raise OSError("no program to open files with")
            subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise Refused(HTTPStatus.CONFLICT, f"Could not open the file: {exc}") from None
    return {"opened": str(path)}


class Handler(BaseHTTPRequestHandler):
    server_version = "jobfinder-ui"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - the base class's name
        pass

    # --- plumbing ---
    def _send(self, status: HTTPStatus, body: bytes, content_type: str, extra: dict | None = None, csp: str | None = CSP) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _guard(self, post: bool, token: str | None) -> None:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            raise Refused(HTTPStatus.FORBIDDEN, "Unexpected Host header.")
        origin = self.headers.get("Origin")
        if post and origin != f"http://{host}":
            raise Refused(HTTPStatus.FORBIDDEN, "Unexpected Origin.")
        if token is not None and not hmac.compare_digest(token, self.server.token):
            raise Refused(HTTPStatus.FORBIDDEN, "Bad token.")

    def _body(self, limit: int = MAX_BODY) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise Refused(HTTPStatus.BAD_REQUEST, "Body must be JSON.") from None
        if not isinstance(body, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, "Body must be a JSON object.")
        return body

    def _run(self, post: bool) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            token = None
            if url.path.startswith("/api/"):  # the stream and a document are opened by the browser, which cannot set headers
                token = query.get("token", [""])[0] if url.path in ("/api/term/stream", "/api/doc") else self.headers.get("X-Token", "")
            self._guard(post, token)
            if post:
                self._post(url.path)
            else:
                self._get(url.path, query)
        except Refused as exc:
            self._json({"ok": False, "error": exc.message}, exc.status)
        except SETTINGS_ERRORS as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):  # noqa: N802
        self._run(False)

    def do_POST(self):  # noqa: N802
        self._run(True)

    # --- routes ---
    def _get(self, path: str, query: dict) -> None:
        if path == "/":
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
            return self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/vendor/") and path[len("/vendor/"):] in VENDOR:
            name = path[len("/vendor/"):]
            kind = "text/css" if name.endswith(".css") else "text/javascript"
            return self._send(HTTPStatus.OK, (STATIC / "vendor" / name).read_bytes(), kind + "; charset=utf-8")
        if path == "/api/state":
            return self._json({"ok": True, **state(self.server.sessions)})
        if path == "/api/cost":
            return self._json({"ok": True, **cost_state()})
        if path == "/api/term/stream":
            return self._stream(query)
        if path == "/api/doc":
            return self._document(query)
        raise Refused(HTTPStatus.NOT_FOUND, "Not found.")

    def _post(self, path: str) -> None:
        if path == "/api/approve":
            return self._json({"ok": True, **approve(self._body())})
        if path == "/api/interview":
            return self._json({"ok": True, **interview(self._body())})
        if path == "/api/delete":
            return self._json({"ok": True, **delete(self._body())})
        if path == "/api/open-doc":
            return self._json({"ok": True, **open_document(self._body())})
        if path == "/api/mark-applied":
            return self._json({"ok": True, **mark_applied(self._body())})
        if path.startswith("/api/settings/") and path.rsplit("/", 1)[1] in SAVERS:
            return self._json({"ok": True, "saved": SAVERS[path.rsplit("/", 1)[1]](self._body())})
        if path == "/api/searches":
            return self._json({"ok": True, "saved": save_searches(self._body())})
        if path == "/api/cv-upload":
            return self._json({"ok": True, **upload_cv(self._body(CV_MAX * 4 // 3 + 4096))})
        if path == "/api/apify-token":
            return self._json({"ok": True, **save_apify_token(self._body())})
        if path == "/api/safety-model":
            return self._json({"ok": True, **install_safety_model()})
        if path == "/api/source-check":
            try:
                return self._json(check(make_client()))
            except SourceError as exc:
                return self._json({"ok": False, "error": str(exc)})
        if path.startswith("/api/term/"):
            return self._terminal(path.rsplit("/", 1)[1], self.server.sessions, self._body())
        raise Refused(HTTPStatus.NOT_FOUND, "Not found.")

    def _terminal(self, action: str, sessions: terminal.Sessions, body: dict) -> None:
        try:
            if action == "start":
                session = sessions.start(str(body.get("command")), int(body.get("cols") or 100), int(body.get("rows") or 30))
                return self._json({"ok": True, "id": session.id, **sessions.status()})
            session = sessions.get(body.get("id"))
            if action == "input":
                session.write(str(body.get("data", "")))
            elif action == "resize":
                session.resize(int(body.get("cols") or 100), int(body.get("rows") or 30))
            elif action == "stop":
                session.stop()
            elif action == "close":
                sessions.close(session.id)
            else:
                raise Refused(HTTPStatus.NOT_FOUND, "Not found.")
        except terminal.TerminalError as exc:
            raise Refused(HTTPStatus.CONFLICT, str(exc)) from None
        except (TypeError, ValueError):
            raise Refused(HTTPStatus.BAD_REQUEST, "Bad terminal request.") from None
        self._json({"ok": True, **sessions.status()})

    def _document(self, query: dict) -> None:
        """One of a posting's documents, for the browser to show (a PDF in its viewer, text as text)."""
        job_id, kind = query.get("id", [""])[0], query.get("kind", [""])[0]
        job = next((j for j in jobs.load(JOBS_JSON)["jobs"] if j["id"] == job_id), None)
        path = document_path(job, kind) if job else None
        if path is None:
            raise Refused(HTTPStatus.NOT_FOUND, "No such document.")
        kind_type = DOC_TYPES[path.suffix.lower()]
        # a PDF's viewer does not run under our policy; text is sandboxed so nothing in it can run
        self._send(HTTPStatus.OK, path.read_bytes(), kind_type, {"Content-Disposition": f'inline; filename="{path.name}"'},
                   csp=None if path.suffix.lower() == ".pdf" else "sandbox")

    def _stream(self, query: dict) -> None:
        """Server-sent events: `data` carries base64 terminal output, `id` the offset to resume from."""
        try:
            pos = int(self.headers.get("Last-Event-ID") or query.get("from", ["0"])[0])
        except ValueError:
            pos = 0
        self.send_response(HTTPStatus.OK)
        for key, value in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"),
                           ("Connection", "close"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        try:
            session = self.server.sessions.get(query.get("id", [""])[0])
        except terminal.TerminalError:
            return self.wfile.write(b"event: exit\ndata: -1\n\n")
        while not self.server.closing.is_set():
            data, pos, code = session.read_from(pos, timeout=10)
            if data:
                self.wfile.write(f"id: {pos}\ndata: {base64.b64encode(data).decode()}\n\n".encode())
            elif code is not None:
                self.wfile.write(f"event: exit\ndata: {code}\n\n".encode())
            else:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
            if code is not None and not data:
                return


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), Handler)
        self.token = secrets.token_urlsafe(24)
        self.sessions = terminal.Sessions()
        self.closing = threading.Event()

    def close(self) -> None:
        self.closing.set()
        self.sessions.stop_all()
        self.server_close()


def serve(port: int, explicit: bool, open_browser: bool) -> int:
    """Start on `port` (when none was asked for, the next free one of ten), print the address, run until Ctrl+C."""
    server = None
    for candidate in range(port, port + (1 if explicit else 10)):
        try:
            server = Server(candidate)
            break
        except OSError:
            continue
    if server is None:
        print(f"Port {port} is in use. Pick another with --port.", flush=True)
        return 1
    address = f"http://127.0.0.1:{server.server_port}/"
    print(f"Job Finder UI: {address}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0
