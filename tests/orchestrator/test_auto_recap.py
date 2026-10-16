"""Standalone CLI boundaries and real CPU rendering with synthetic provider responses."""
import base64
import io
import json
from pathlib import Path
import sys
import urllib.error
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import auto_recap
import auto_media
from auto_mimo import MiMo


@pytest.mark.parametrize("clip", [
    {"scene_id": -1, "duration": 3, "text": "测试"},
    {"scene_id": True, "duration": 3, "text": "测试"},
    {"scene_id": 0, "duration": float("nan"), "text": "测试"},
    {"scene_id": 0, "duration": 8, "text": "测试"},
    {"scene_id": 0, "duration": 3, "text": "坏\n字幕"},
    {"scene_id": 0, "duration": 3, "text": "<i>坏字幕</i>"},
])
def test_plan_rejects_untrusted_model_fields(clip):
    with pytest.raises(ValueError):
        auto_recap.validate_plan({"clips": [clip]}, [{"start": 0, "end": 4}], 3)


def test_plan_rejects_duplicate_and_wrong_total():
    clip = {"scene_id": 0, "duration": 3, "text": "测试"}
    for clips, target in [([clip, clip], 6), ([clip], 4)]:
        with pytest.raises(ValueError):
            auto_recap.validate_plan({"clips": clips}, [{"start": 0, "end": 4}], target)


def test_preflight_missing_key_and_tool(monkeypatch):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MIMO_API_KEY"):
        MiMo()
    monkeypatch.setattr(auto_media.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="ffmpeg is missing"):
        auto_media.check_tools()


def test_provider_errors_do_not_disclose_response_or_key(monkeypatch):
    monkeypatch.setenv("MIMO_API_KEY", "synthetic-secret")
    monkeypatch.delenv("MIMO_API_URL", raising=False)
    client = MiMo()
    class Denied:
        def open(self, request, timeout):
            assert request.headers["Api-key"] == "synthetic-secret"
            raise urllib.error.HTTPError(request.full_url, 401, "synthetic-secret", {}, None)
    client.opener = Denied()
    with pytest.raises(RuntimeError, match="HTTP 401") as caught:
        client.text("test")
    assert "synthetic-secret" not in str(caught.value)


def test_provider_wire_shapes_and_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMO_API_KEY", "synthetic")
    monkeypatch.setenv("MIMO_API_URL", "http://127.0.0.1:1234/v1")
    client = MiMo()
    requests = []
    class Responses:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            requests.append(payload)
            message = {"content": "测试"}
            if payload["model"].endswith("tts"):
                message = {"audio": {"data": base64.b64encode(b"synthetic-wave").decode()}}
            return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())
    client.opener = Responses()
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"synthetic-input")
    assert client.transcribe(audio) == "测试"
    client.speak("你好", tmp_path / "voice.wav")
    assert requests[0]["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert requests[1]["messages"][1] == {"role": "assistant", "content": "你好"}
    assert (tmp_path / "voice.wav").read_bytes() == b"synthetic-wave"


def test_multisource_pipeline_with_synthetic_mimo(monkeypatch, tmp_path):
    # API output is synthetic; all decoding, detection, sampling, mixing, subtitles,
    # probing and draft generation use the real production pipeline and real ffmpeg.
    first, second = tmp_path / "has audio.mp4", tmp_path / "silent's video.mp4"
    auto_media.demo_source(first)
    auto_media.ffmpeg("-i", first, "-an", "-c:v", "copy", second)
    seen = {"asr": 0, "vision": 0, "tts": 0}
    class SyntheticMiMo:
        def transcribe(self, path):
            assert auto_media.probe(path)[0] > 0
            seen["asr"] += 1
            return "测试音频"
        def text(self, content):
            assert len(content) == 4
            seen["vision"] += 1
            return "画面是彩色测试图案。"
        def json(self, prompt):
            return {"clips": [{"scene_id": 0, "duration": 2, "text": "第一段测试图案。"},
                              {"scene_id": 1, "duration": 2, "text": "第二段没有原声。"}]}
        def speak(self, text, path):
            seen["tts"] += 1
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(b"\0\0" * 16000)
    monkeypatch.setattr(auto_recap, "MiMo", SyntheticMiMo)
    output = tmp_path / "output's folder"
    assert auto_recap.main([str(first), str(second), "--seconds", "4", "--out", str(output),
                            "--draft", "--burn-subtitles"]) == 0
    assert seen == {"asr": 1, "vision": 2, "tts": 2}
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert abs(result["actual_seconds"] - 4) < .2
    assert result["demo"] is False
    timeline = json.loads((output / "timeline.json").read_text(encoding="utf-8"))
    assert len(timeline["tracks"][0]["clips"]) == 2
    assert len(timeline["tracks"][2]["segments"]) == 1
    assert "第一段测试图案" in (output / "recap.srt").read_text(encoding="utf-8")
    assert (output / "draft/recap/draft_content.json").is_file()
    # A second run cannot overwrite an existing result.
    assert auto_recap.main([str(first), "--seconds", "4", "--out", str(output)]) == 1
    assert json.loads((output / "result.json").read_text(encoding="utf-8")) == result
