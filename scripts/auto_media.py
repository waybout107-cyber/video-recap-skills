"""CPU media operations for auto_recap; no shell interpolation."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path


def run(args, cwd=None):
    try:
        result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=7200)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{args[0]} exceeded the two-hour processing limit.") from None
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-2000:]}")
    return result.stdout, result.stderr


def ffmpeg(*args, cwd=None):
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], cwd=cwd)


def check_tools(burn=False):
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} is missing from PATH. Install ffmpeg; see docs/standalone-recap.md.")
    encoders, _ = run(["ffmpeg", "-hide_banner", "-encoders"])
    if not all(re.search(rf"\b{codec}\b", encoders) for codec in ("libx264", "aac", "mov_text")):
        raise ValueError("ffmpeg needs the libx264, AAC and mov_text encoders.")
    filters, _ = run(["ffmpeg", "-hide_banner", "-filters"])
    if burn and not re.search(r"\bsubtitles\b", filters):
        raise ValueError("--burn-subtitles needs ffmpeg with libass; use ffmpeg-full on macOS.")


def probe(path):
    stdout, _ = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    data = json.loads(stdout)
    duration = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Cannot determine positive duration: {path}")
    return duration, data["streams"]


def scenes(path, duration):
    # Decoder timestamps are normalized so scene boundaries share the seek clock.
    _, log = ffmpeg("-i", path, "-an", "-vf", "setpts=PTS-STARTPTS,select='gt(scene,0.3)',showinfo",
                    "-f", "null", "-")
    cuts = sorted({0.0, duration, *(float(t) for t in re.findall(r"pts_time:([0-9.]+)", log)
                                  if 0 < float(t) < duration)})
    spans = []
    for start, end in zip(cuts, cuts[1:]):
        # Bound each ASR upload, and sample long takes throughout their duration.
        count = max(1, math.ceil((end - start) / 12))
        for index in range(count):
            a = start + (end - start) * index / count
            b = start + (end - start) * (index + 1) / count
            if b - a >= .4:
                spans.append((a, b))
    return spans


def sample_scene(source, start, end, directory, has_audio):
    directory.mkdir()
    frames = []
    for index, fraction in enumerate((.15, .5, .85)):
        frame = directory / f"frame{index}.jpg"
        ffmpeg("-ss", start + (end - start) * fraction, "-i", source, "-frames:v", "1",
               "-vf", "scale=640:-2", frame)
        frames.append(frame)
    audio = directory / "speech.wav"
    if has_audio:
        ffmpeg("-ss", start, "-i", source, "-t", end - start, "-vn", "-ac", "1",
               "-ar", "16000", "-c:a", "pcm_s16le", audio)
    return frames, audio if has_audio else None


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def render(plan, evidence, directory, burn=False):
    video_clips, audio_segments, subtitles = [], [], []
    cursor = 0.0
    for index, clip in enumerate(plan):
        scene = evidence[clip["scene_id"]]
        duration = clip["duration"]
        source = scene["source"]
        voice = directory / f"voice{index}.wav"
        spoken, _ = probe(voice)
        if spoken > duration + .03:
            raise ValueError(f"Narration {index} is longer than its clip; refusing to truncate speech.")
        clean = directory / f"picture{index}.mp4"
        video_filter = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
        ffmpeg("-ss", scene["start"], "-i", source, "-t", duration, "-an", "-vf", video_filter,
               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", clean)
        mixed = directory / f"part{index}.mp4"
        inputs = ["-i", clean, "-i", voice]
        if scene["has_audio"]:
            inputs += ["-ss", scene["start"], "-i", source]
            mix = (f"[1:a]apad,atrim=duration={duration},asetpts=PTS-STARTPTS[n];"
                   f"[2:a]atrim=duration={duration},asetpts=PTS-STARTPTS,volume=0.12[o];"
                   "[n][o]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0[a]")
        else:
            mix = f"[1:a]apad,atrim=duration={duration},alimiter=limit=0.95:level=0[a]"
        ffmpeg(*inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-t", duration,
               "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", mixed)
        video_clips.append({"source_path": str(clean), "source_start": 0, "source_end": duration,
                            "timeline_start": cursor, "timeline_end": cursor + duration,
                            "audio": {"base_gain": 0}})
        audio_segments.append({"source_path": str(voice), "timeline_start": cursor,
                               "timeline_end": cursor + spoken, "gain": 1, "text": clip["text"]})
        # One short narration per clip, cue timed to measured speech, not the entire slot.
        subtitles.append({"text": clip["text"], "timeline_start": cursor, "timeline_end": cursor + spoken})
        cursor += duration
    srt = "\n\n".join(f"{i+1}\n{timestamp(s['timeline_start'])} --> {timestamp(s['timeline_end'])}\n{s['text']}"
                       for i, s in enumerate(subtitles)) + "\n"
    (directory / "recap.srt").write_text(srt, encoding="utf-8")
    (directory / "parts.txt").write_text("".join(f"file 'part{i}.mp4'\n" for i in range(len(plan))), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "1", "-i", "parts.txt", "-c", "copy", "joined.mp4", cwd=directory)
    args = ["-i", "joined.mp4", "-i", "recap.srt", "-map", "0:v", "-map", "0:a", "-map", "1:0"]
    if burn:
        args += ["-vf", "subtitles=recap.srt", "-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    else:
        args += ["-c:v", "copy"]
    ffmpeg(*args, "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=zho",
           "-movflags", "+faststart", "recap.mp4", cwd=directory)
    # Preserve the rendered source mix on its own editable track.
    original_segments = []
    cursor = 0.0
    for index, clip in enumerate(plan):
        scene = evidence[clip["scene_id"]]
        if scene["has_audio"]:
            audio = directory / f"original{index}.wav"
            ffmpeg("-ss", scene["start"], "-i", scene["source"], "-t", clip["duration"],
                   "-vn", "-af", "volume=0.12", "-ar", "48000", "-ac", "2", audio)
            original_segments.append({"source_path": str(audio), "timeline_start": cursor,
                                      "timeline_end": cursor + clip["duration"], "gain": 1})
        cursor += clip["duration"]
    timeline = {"schema_version": 2, "canvas": {"width": 1280, "height": 720, "fps": 30},
                "duration": cursor, "tracks": [
                    {"kind": "video", "clips": video_clips},
                    {"kind": "audio", "role": "narration", "segments": audio_segments},
                    {"kind": "audio", "role": "original", "segments": original_segments},
                    {"kind": "text", "segments": subtitles},
                ]}
    (directory / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    actual, streams = probe(directory / "recap.mp4")
    if abs(actual - cursor) > max(.3, len(plan) * .05):
        raise RuntimeError(f"Output duration {actual:.2f}s differs from planned {cursor:.2f}s.")
    if not {"video", "audio", "subtitle"}.issubset({s["codec_type"] for s in streams}):
        raise RuntimeError("Output is missing a required video/audio/subtitle stream.")
    ffmpeg("-v", "error", "-i", directory / "recap.mp4", "-f", "null", "-")
    return actual


def demo_source(path):
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=12",
           "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=12",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", Path(path))
