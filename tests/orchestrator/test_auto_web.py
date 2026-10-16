"""HTTP boundaries for the mobile recap interface; no real cloud credentials."""
import base64
import http.client
import os
import subprocess
import json
from pathlib import Path
import re
import sys
import threading
import time
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import auto_web


@pytest.fixture
def web(tmp_path):
    server = auto_web.WebServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, method, path, body=None, token=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
    headers = dict(headers or {})
    if token:
        headers["X-Recap-Session"] = token
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    connection.request(method, path, body, headers)
    response = connection.getresponse()
    status, payload, response_headers = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return status, payload, response_headers


def session(server):
    status, page, headers = request(server, "GET", "/")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    return re.search(rb"const token='([a-f0-9]{64})'", page)[1].decode()


def test_uploads_and_status_are_isolated_by_tab(web):
    first, other = session(web), session(web)
    assert first != other
    status, payload, _ = request(web, "POST", "/api/upload", b"video fixture", first)
    assert status == 201
    upload_id = json.loads(payload)["id"]
    stored = web.sessions[first]["uploads"][upload_id]
    assert stored.read_bytes() == b"video fixture"
    assert stored.stat().st_mode & 0o777 == 0o600
    assert request(web, "POST", "/api/upload", b"fixture")[0] == 400
    body = {"key": "synthetic", "seconds": 3, "uploads": [upload_id]}
    assert request(web, "POST", "/api/start", body, other)[0] == 400
    # Job IDs alone do not expose progress or errors across tabs.
    web.jobs["example"] = {"state": "running"}
    web.sessions[first]["jobs"].append("example")
    assert request(web, "GET", "/api/job/example", token=other)[0] == 404
    assert request(web, "GET", "/api/job/example", token=first)[0] == 200


@pytest.mark.parametrize("body", [
    {"seconds": 8, "uploads": []},
    {"demo": True, "seconds": 13},
    {"demo": True, "seconds": float("nan")},
    {"demo": "true", "seconds": 8},
    {"demo": True, "seconds": 8, "cluster": "bad"},
    {"key": "invalid\nkey", "seconds": 8},
])
def test_invalid_start_never_launches_a_process(web, monkeypatch, body):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid HTTP input must not launch a worker")
    monkeypatch.setattr(web, "execute", forbidden)
    status, payload, _ = request(web, "POST", "/api/start", body, session(web))
    assert status == 400
    assert "error" in json.loads(payload)
    assert not web.jobs


def test_upload_limits_and_malformed_json(web, monkeypatch):
    token = session(web)
    monkeypatch.setattr(auto_web, "MAX_FILE_BYTES", 3)
    assert request(web, "POST", "/api/upload", b"large fixture", token)[0] == 400
    assert request(web, "POST", "/api/start", b'{"key":"secret",broken}', token)[1] == b'{"error": "Invalid request."}'
    assert not (web.data_dir / "uploads").exists()
    assert not web.sessions[token]["uploads"]


def test_child_credentials_are_not_persisted_or_returned(web, monkeypatch):
    token = session(web)
    uploaded = request(web, "POST", "/api/upload", b"fixture", token)
    upload_id = json.loads(uploaded[1])["id"]
    key = "synthetic-private-key"
    monkeypatch.setenv("MIMO_API_URL", "https://untrusted.invalid/v1")
    class RejectedRun:
        def __init__(self, args, **options):
            assert key not in str(args)
            assert options["env"]["MIMO_API_KEY"] == key
            assert "MIMO_API_URL" not in options["env"]
            self.stdout = iter([f"Error: unexpected diagnostic {key}\n"])
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def wait(self):
            return 1
    monkeypatch.setattr(auto_web.subprocess, "Popen", RejectedRun)
    body = {"key": key, "seconds": 3, "uploads": [upload_id]}
    status, response, _ = request(web, "POST", "/api/start", body, token)
    assert status == 202
    job_id = json.loads(response)["id"]
    for _ in range(100):
        if web.jobs[job_id]["state"] == "failed":
            break
        time.sleep(.01)
    status, result, _ = request(web, "GET", f"/api/job/{job_id}", token=token)
    assert status == 200
    assert json.loads(result)["state"] == "failed"
    assert key.encode() not in result
    assert "[hidden]" in json.loads(result)["error"]
    assert all(key.encode() not in p.read_bytes() for p in web.data_dir.rglob("*") if p.is_file())


def test_browser_api_offline_demo_and_range_download(web):
    token = session(web)
    status, result, _ = request(web, "POST", "/api/start", {
        "demo": True, "seconds": 2, "draft": True, "burn": True,
    }, token)
    assert status == 202
    job_id = json.loads(result)["id"]
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        status, result, _ = request(web, "GET", f"/api/job/{job_id}", token=token)
        job = json.loads(result)
        if job["state"] in {"complete", "failed"}:
            break
        time.sleep(.1)
    assert job["state"] == "complete", job
    assert job["webm"] is True
    assert request(web, "GET", f"/files/{job_id}/preview.webm")[0] == 200
    status, full, headers = request(web, "GET", f"/files/{job_id}/recap.mp4")
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    status, chunk, headers = request(web, "GET", f"/files/{job_id}/recap.mp4", headers={"Range": "bytes=0-19"})
    assert status == 206 and chunk == full[:20]
    assert headers["Content-Range"].startswith("bytes 0-19/")
    assert request(web, "GET", f"/files/{job_id}/recap.mp4", headers={"Range": "bytes=20-5"})[0] == 416
    assert request(web, "GET", f"/files/{job_id}/../../auto_web.py")[0] == 404
    assert request(web, "GET", f"/files/{job_id}/analysis.json")[0] == 404
    status, srt, _ = request(web, "GET", f"/files/{job_id}/recap.srt")
    assert status == 200 and "离线合成示例".encode() in srt
    status, archive, _ = request(web, "GET", f"/files/{job_id}/draft.zip")
    assert status == 200 and len(archive) > 0
    with zipfile.ZipFile(web.data_dir / job_id / "result/draft.zip") as draft:
        assert "recap/draft_content.json" in draft.namelist()


def test_hosted_password_protects_pages_uploads_and_downloads(web):
    web.password = "synthetic-website-password"
    basic = "Basic " + base64.b64encode(("recap:" + web.password).encode()).decode()
    for method, path in [("GET", "/"), ("POST", "/api/upload"),
                         ("GET", "/api/job/example"), ("GET", "/files/example/recap.mp4")]:
        status, _, headers = request(web, method, path)
        assert status == 401
        assert headers["WWW-Authenticate"].startswith("Basic ")
    assert request(web, "GET", "/health")[0] == 200
    assert request(web, "GET", "/", headers={"Authorization": "Basic broken"})[0] == 401
    status, page, _ = request(web, "GET", "/", headers={"Authorization": basic})
    assert status == 200 and b"MiMo API key" in page
    assert web.password.encode() not in page


def test_container_start_requires_a_website_password():
    env = dict(os.environ)
    env.pop("RECAP_WEB_PASSWORD", None)
    result = subprocess.run([sys.executable, str(auto_web.SCRIPT_DIR / "auto_web.py"),
                             "--require-auth"], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert "RECAP_WEB_PASSWORD" in result.stderr
