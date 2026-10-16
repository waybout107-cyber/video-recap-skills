"""Small MiMo adapter for the opt-in standalone recap command (stdlib only)."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


class MiMo:
    def __init__(self):
        self.key = os.environ.get("MIMO_API_KEY", "").strip()
        if not self.key:
            raise ValueError("MIMO_API_KEY is missing. Set it to your Xiaomi MiMo API key.")
        cluster = os.environ.get("MIMO_TOKEN_PLAN_CLUSTER", "cn")
        if cluster not in {"cn", "sgp", "ams"}:
            raise ValueError("MIMO_TOKEN_PLAN_CLUSTER must be cn, sgp or ams.")
        default = (f"https://token-plan-{cluster}.xiaomimimo.com/v1"
                   if self.key.startswith("tp-") else "https://api.xiaomimimo.com/v1")
        self.url = os.environ.get("MIMO_API_URL", default).rstrip("/")
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("MIMO_API_URL requires HTTPS (HTTP allowed only on loopback for tests).")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MIMO_API_URL must not contain credentials, query or fragment.")
        if not self.url.endswith("/chat/completions"):
            self.url += "/chat/completions"
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url, data, headers={
            "Content-Type": "application/json", "api-key": self.key,
        })
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=180) as response:
                    result = json.load(response)
                message = result["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError("invalid message")
                return message
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    f"MiMo HTTP {exc.code}. Check key/model access, credit and API endpoint; "
                    "429 means rate limited. Analysis files remain in the output directory."
                ) from None
            except (urllib.error.URLError, TimeoutError):
                raise RuntimeError("MiMo connection failed or timed out; check your network.") from None
            except (KeyError, IndexError, TypeError, ValueError):
                raise RuntimeError("MiMo returned an invalid response; no output was accepted.") from None

    def text(self, content, model="mimo-v2.5"):
        result = self.call({"model": model, "messages": [{"role": "user", "content": content}]})
        text = result.get("content")
        if not isinstance(text, str):
            raise RuntimeError("MiMo returned no text.")
        return text.strip()

    def json(self, prompt):
        text = self.text(prompt + "\n只返回 JSON，不要 Markdown。")
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except ValueError:
            raise RuntimeError("MiMo returned invalid plan JSON. See analysis.json and retry in a new directory.") from None

    def transcribe(self, path):
        return self.text([{"type": "input_audio", "input_audio": {
            "data": data_uri(path, "audio/wav"),
        }}], "mimo-v2.5-asr")

    def speak(self, text, path):
        result = self.call({
            "model": "mimo-v2.5-tts",
            "messages": [{"role": "user", "content": "请用自然清晰的普通话解说，语速适中。"},
                         {"role": "assistant", "content": text}],
            "audio": {"format": "wav", "voice": os.environ.get("MIMO_TTS_VOICE", "冰糖")},
        })
        try:
            path.write_bytes(base64.b64decode(result["audio"]["data"], validate=True))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("MiMo TTS returned invalid audio.") from None


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
