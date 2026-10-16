#!/usr/bin/env python3
"""Mobile-friendly stdlib web interface for auto_recap.py."""
import argparse
import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import zipfile

from auto_media import check_tools, ffmpeg

SCRIPT_DIR = Path(__file__).resolve().parent
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_SESSION_BYTES = 4 * 1024 ** 3


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data_dir, password=""):
        super().__init__(address, Handler)
        self.password = password
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sessions = {}
        self.jobs = {}
        self.lock = threading.RLock()
        self.worker_lock = threading.Lock()

    def new_session(self):
        with self.lock:
            # Active sessions last for this process. Bound unused-tab accumulation.
            if len(self.sessions) >= 100:
                raise ValueError("Too many open sessions. Restart the web app to clear them.")
            token = secrets.token_hex(32)
            self.sessions[token] = {"uploads": {}, "jobs": [], "bytes": 0, "busy": False}
            return token

    def launch(self, token, body):
        key = body.get("key", "")
        demo = body.get("demo", False)
        seconds = body.get("seconds", 60)
        if type(demo) is not bool or type(seconds) not in (int, float) or not math.isfinite(seconds):
            raise ValueError("Choose a valid duration and run mode.")
        if seconds < 2 or seconds > (12 if demo else 3600):
            raise ValueError("Choose 2–3600 seconds (2–12 seconds for the offline demo).")
        if not demo and (not isinstance(key, str) or not key.strip() or len(key) > 4000
                         or any(c.isspace() or ord(c) < 32 for c in key)):
            raise ValueError("Paste your MiMo API key to create a narrated recap.")
        cluster = body.get("cluster", "cn")
        if cluster not in {"cn", "sgp", "ams"}:
            raise ValueError("Choose a valid MiMo cluster.")
        with self.lock:
            session = self.sessions[token]
            if session["busy"]:
                raise ValueError("A recap is already queued or running in this tab.")
            ids = body.get("uploads", [])
            if not isinstance(ids, list) or len(ids) > 20 or any(
                not isinstance(item, str) or item not in session["uploads"] for item in ids
            ):
                raise ValueError("Upload your videos again in this tab.")
            if not demo and not ids:
                raise ValueError("Choose at least one video first.")
            job_id = secrets.token_hex(16)
            job_dir = self.data_dir / job_id
            job_dir.mkdir(mode=0o700)
            paths = [session["uploads"][item] for item in ids]
            job = {"id": job_id, "state": "queued", "progress": "Waiting to start…", "error": ""}
            self.jobs[job_id] = job
            session["jobs"].append(job_id)
            session["busy"] = True
        threading.Thread(target=self.execute, args=(token, job, paths, body, key, cluster), daemon=True).start()
        return job_id

    def execute(self, token, job, paths, body, key, cluster):
        try:
            # One CPU render at a time; credentials stay only in worker memory/env.
            with self.worker_lock:
                job["state"] = "running"
                job["progress"] = "Checking inputs and preparing your recap…"
                output = self.data_dir / job["id"] / "result"
                command = [sys.executable, "-u", str(SCRIPT_DIR / "auto_recap.py"),
                           *map(str, paths), "--seconds", str(body.get("seconds", 60)), "--out", str(output)]
                for option, flag in (("demo", "--demo"), ("draft", "--draft"), ("burn", "--burn-subtitles")):
                    if body.get(option) is True:
                        command.append(flag)
                env = dict(os.environ)
                # Web credentials always go to official MiMo, never inherited overrides.
                env.pop("MIMO_API_URL", None)
                env.pop("RECAP_WEB_PASSWORD", None)
                env["MIMO_API_KEY"] = "" if body.get("demo") else key
                env["MIMO_TOKEN_PLAN_CLUSTER"] = cluster
                with subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                      errors="replace") as process:
                    del env
                    # Drain stdout without storing user text or credentials in logs/files.
                    stage = "Preparing your recap…"
                    error = ""
                    for line in process.stdout:
                        if line.startswith("Analyzing "):
                            stage = "Understanding scenes and dialogue…"
                        elif line.startswith("Narrating clip "):
                            stage = line.strip()  # Only clip counts, no user text.
                        elif line.startswith("Finished:"):
                            stage = "Finishing your video…"
                        elif line.startswith("Error:"):
                            error = line.strip().replace(key, "[hidden]") if key else line.strip()
                        job["progress"] = stage
                    code = process.wait()
                if code != 0:
                    raise ValueError(error or "Recap failed. Check your video, API credit and model access, then retry.")
                job["progress"] = "Preparing a phone-friendly preview…"
                try:
                    # Some shared Chromium builds cannot decode H.264/AAC. Keep
                    # the MP4 delivery and add a small broadly supported WebM.
                    ffmpeg("-i", output / "recap.mp4", "-vf", "scale=640:-2",
                           "-c:v", "libvpx", "-b:v", "650k", "-deadline", "realtime",
                           "-cpu-used", "5", "-c:a", "libopus", "-b:a", "64k", output / "preview.webm")
                    job["webm"] = True
                except RuntimeError:
                    job["webm"] = False  # Downloadable MP4 remains a valid delivery.
                if body.get("draft"):
                    with zipfile.ZipFile(output / "draft.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                        for path in (output / "draft").rglob("*"):
                            if path.is_file():
                                archive.write(path, path.relative_to(output / "draft"))
                job["state"], job["progress"] = "complete", "Your recap is ready."
        except Exception as exc:
            job["state"] = "failed"
            # Generic unexpected errors avoid leaking env values/stack traces.
            job["error"] = str(exc) if isinstance(exc, ValueError) else "The recap could not finish. Please retry."
        finally:
            key = ""
            body.clear()
            with self.lock:
                self.sessions[token]["busy"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # No request paths, upload names or credentials in access logs.

    def send(self, status, value, content_type="application/json; charset=utf-8"):
        data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def session(self):
        token = self.headers.get("X-Recap-Session", "")
        if token not in self.server.sessions:
            raise ValueError("Session expired. Refresh this page and upload your videos again.")
        return token

    def authenticated(self):
        if not self.server.password:
            return True
        supplied = self.headers.get("Authorization", "")
        try:
            scheme, encoded = supplied.split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError
            decoded = base64.b64decode(encoded, validate=True)
            expected = ("recap:" + self.server.password).encode("utf-8")
            valid = secrets.compare_digest(decoded, expected)
        except (ValueError, binascii.Error):
            valid = False
        if not valid:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Video Recap", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True
        return valid

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path != "/health" and not self.authenticated():
                return
            if path == "/":
                token = self.server.new_session()
                page = (SCRIPT_DIR / "auto_web.html").read_text(encoding="utf-8")
                self.send(200, page.replace("__SESSION__", token).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/health":
                self.send(200, {"status": "ok"})
            elif path.startswith("/api/job/"):
                token = self.session()
                job_id = path.rsplit("/", 1)[1]
                if job_id not in self.server.sessions[token]["jobs"]:
                    self.send(404, {"error": "Job not found."})
                    return
                self.send(200, self.server.jobs[job_id])
            elif path.startswith("/files/"):
                self.download(path)
            else:
                self.send(404, {"error": "Not found."})
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            if not self.authenticated():
                return
            self.connection.settimeout(300)
            token = self.session()  # Custom header prevents cross-origin form submissions.
            path = urlsplit(self.path).path
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("A known upload size is required.")
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/upload":
                self.upload(token, length)
            elif path == "/api/start":
                if not 0 < length <= 16384:
                    raise ValueError("Invalid request size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Invalid request.")
                self.send(202, {"id": self.server.launch(token, body)})
            else:
                self.send(404, {"error": "Not found."})
        except (ValueError, KeyError) as exc:
            # Body data and secrets never included in JSON parser errors.
            message = "Invalid request." if isinstance(exc, (json.JSONDecodeError, KeyError)) else str(exc)
            self.close_connection = True
            self.send(400, {"error": message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def upload(self, token, length):
        if not 0 < length <= MAX_FILE_BYTES:
            raise ValueError("Choose a video under 2 GB.")
        with self.server.lock:
            session = self.server.sessions[token]
            if session["busy"] or len(session["uploads"]) >= 20 or session["bytes"] + length > MAX_SESSION_BYTES:
                raise ValueError("This tab is busy or reached its upload limit (20 videos, 4 GB). Refresh to start a new session.")
            session["bytes"] += length
        upload_id = secrets.token_hex(16)
        directory = self.server.data_dir / "uploads"
        directory.mkdir(mode=0o700, exist_ok=True)
        # Extension/name provided by clients is never used as a filesystem path.
        path = directory / (upload_id + ".video")
        remaining = length
        try:
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Upload interrupted. Choose the video again.")
                    output.write(chunk)
                    remaining -= len(chunk)
            with self.server.lock:
                session["uploads"][upload_id] = path
        except Exception:
            path.unlink(missing_ok=True)
            with self.server.lock:
                session["bytes"] -= length
            raise
        self.send(201, {"id": upload_id})

    def download(self, route):
        parts = route.split("/")
        if len(parts) != 4:
            self.send(404, {"error": "Not found."})
            return
        _, _, job_id, filename = parts
        types = {"recap.mp4": "video/mp4", "recap.srt": "application/x-subrip", "draft.zip": "application/zip",
                 "preview.webm": "video/webm"}
        job = self.server.jobs.get(job_id)
        if not job or job["state"] != "complete" or filename not in types:
            self.send(404, {"error": "File not found."})
            return
        path = self.server.data_dir / job_id / "result" / filename
        if not path.is_file():
            self.send(404, {"error": "File not found."})
            return
        size = path.stat().st_size
        # Android video players issue range requests for seeking.
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header:
            try:
                unit, bounds = range_header.split("=", 1)
                first, last = bounds.split("-", 1)
                if unit != "bytes" or not first:
                    raise ValueError
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
                if start < 0 or end < start or start >= size:
                    raise ValueError
            except ValueError:
                self.send(416, {"error": "Unsupported byte range."})
                return
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", types[filename])
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename not in {"recap.mp4", "preview.webm"}:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                data = stream.read(min(1024 * 1024, remaining))
                self.wfile.write(data)
                remaining -= len(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=os.environ.get("PORT", "8080"))
    parser.add_argument("--require-auth", action="store_true", help="require RECAP_WEB_PASSWORD at startup")
    parser.add_argument("--data-dir", type=Path, help="private upload/output storage; defaults to temporary directory")
    args = parser.parse_args()
    password = os.environ.get("RECAP_WEB_PASSWORD", "")
    if args.require_auth and len(password) < 12:
        parser.error("Set RECAP_WEB_PASSWORD to a website password of at least 12 characters.")
    check_tools()
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="recap-web-"))
    server = WebServer((args.host, args.port), data_dir, password=password)
    print(f"Video Recap ready at http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.data_dir is None:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
