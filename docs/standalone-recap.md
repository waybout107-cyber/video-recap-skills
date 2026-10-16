# Standalone Chinese recap CLI

Give the command one or more local videos and a target duration. It detects cuts,
transcribes speech, describes sampled frames, asks MiMo to select a coherent story
and write Chinese narration, synthesizes speech, mixes quiet source audio, and
exports H.264/AAC MP4 with a Chinese subtitle track and a separate SRT.
No coding-agent host, GPU, local model, or Python package install is required.

## Setup

Use **Python 3.10+** and an ffmpeg build with **ffprobe, libx264, AAC and mov_text**.
For burned subtitles also install **libass** and a Chinese font such as Noto Sans CJK.

- macOS: `brew install python ffmpeg-full`; add `$(brew --prefix ffmpeg-full)/bin` to PATH.
- Ubuntu/Debian: `sudo apt install python3 ffmpeg fonts-noto-cjk`.
- Windows: install Python 3.10+ and a full ffmpeg build with winget/Scoop; ensure both
  `python` and `ffmpeg`/`ffprobe` work in a new terminal.
- Other Linux distributions: use the distribution's full ffmpeg package or a trusted
  complete build. Some distribution builds omit H.264/AAC for licensing reasons.

From the repository root, set your key in the environment (never in source code):

```bash
export MIMO_API_KEY='your-key'
python3 scripts/auto_recap.py --doctor
```

PowerShell: `$env:MIMO_API_KEY='your-key'`, then use `python` instead of `python3`.
For a `tp-*` Token Plan key, set `MIMO_TOKEN_PLAN_CLUSTER=cn`, `sgp`, or `ams`
(default `cn`). Your account must allow `mimo-v2.5`, `mimo-v2.5-asr` and
`mimo-v2.5-tts`. Model access and costs depend on your MiMo account.
Doctor checks local dependencies and key presence, **not** whether the key is valid.

## Run

```bash
python3 scripts/auto_recap.py '/path/episode 1.mp4' '/path/episode 2.mp4' \
  --seconds 60 --out ./my-recap --draft --burn-subtitles
```

Choose a new or empty output directory. Sources are never modified. A target must
be at least two seconds and no longer than the combined source footage.
Output duration is approximate to frame/container rounding. Each selected scene
is used at most once; a target close to total runtime may fail when very short shots
are excluded. For a quick paid trial, use a 12–30 second video and `--seconds 8`.

`--burn-subtitles` is optional. Without it, enable the embedded Chinese subtitle
track in your player, or load `recap.srt`. Missing Chinese fonts can produce squares
when burning; install a CJK font before rendering.

## Small offline example

```bash
python3 scripts/auto_recap.py --demo --seconds 8 --out ./demo-recap --draft
```

This creates its own test-pattern source and renders an eight-second MP4 with
Chinese subtitles and an editable draft. **Tones stand in for narration**: this
checks local media processing without an API key or network and does not demonstrate
MiMo speech or story quality. Repeat with a fresh output directory.

## Outputs

- `recap.mp4`: finished 1280×720, 30 fps video, narration plus source audio at 12% volume.
- `recap.srt`: Chinese subtitles; one short cue per narration, timed to measured audio.
- `analysis.json`, `scene*/`: evidence, audio samples and three frames per scene.
- `plan.json`: selected scene IDs, durations and the final spoken text.
- `voice*.wav`: normalized narration, retained for editing.
- `timeline.json`: version 2 editable video, narration, original audio and subtitle tracks.
- `draft/recap/`: optional bundled JianYing/CapCut draft. Close the desktop editor,
  copy the folder into its local draft directory, reopen and verify it. Compatibility
  varies with editor version; no desktop-editor import is guaranteed. Video clips
  are normalized extracts; original-source locations remain in `analysis.json`.
- `result.json`: requested/measured duration and successful full decode check.

## Remote data and limits

Normal runs send sampled JPEG frames and up to 12 seconds of mono audio per scene
to **Xiaomi MiMo**, then scene descriptions/transcripts for scriptwriting and text
for TTS. Source path names are excluded from the writing prompt. Uploads can contain
faces, speech and visible text. Use `--demo` or `--doctor` to avoid all remote calls.
`MIMO_API_URL` can override the endpoint (HTTPS; loopback HTTP only for development),
so configure it only for a trusted service. Credentials are sent in the `api-key`
header. `MIMO_TTS_VOICE` changes the preset voice (default `冰糖`); no voice cloning.

Long sources are fully scanned and analyzed, so time/cost grows with source length,
not just target length. Scene analysis samples three frames and can miss brief
motion. Generated narration needs factual review; the automatic mode does not
provide the existing agent workflow's editorial judgment. Subtitles have cue timing,
not word alignment. No BGM is added; source audio is reduced throughout each clip.
The draft keeps separate tracks, but the final output limiter is not an editor effect.

If the model writes too much, the command asks for shorter narration up to twice;
it never truncates speech or silently speeds it up. Invalid plans get two repair
attempts before failing. HTTP 429/5xx gets bounded retries; 401/403 fails with a
key/access hint. Intermediate evidence remains on failure. There is no automatic
resume: inspect the evidence, fix the cause, and choose a fresh output directory.
Network retries may incur provider charges. `--help` lists all CLI options.

The existing agent-led skills remain available for creative control and advanced
editing. This standalone entry point is an explicitly automatic alternative.

## Phone / browser interface

Start the web interface on the computer or server running this project:

```bash
python3 scripts/auto_web.py --port 8080
```

Open `http://127.0.0.1:8080` on that computer, or use the workspace's registered
Preview when running in a cloud coding workspace. In Preview, you can paste the key
into **MiMo API key**, choose videos from your Android file picker, set a duration,
and tap **Create Chinese recap**. No environment-management seat is needed to use
the running application. The offline demo button needs no key.

The server binds to loopback by default. `--host 0.0.0.0` permits a cloud preview
proxy to reach it; use that only behind a trusted access-controlled HTTPS preview.
Do not expose the development server directly to the public Internet. There is no
user-account login: each loaded tab gets a random session token, and finished file
links contain an unguessable job ID. Anyone you share a file link with can open it.

The web form sends your key with the run request and clears the field after a job
starts. It is kept in worker memory and the CLI child environment during that run,
never written to files or browser storage. Normal runs use official Xiaomi MiMo
endpoints, ignoring inherited `MIMO_API_URL` overrides. API and upload requests are
isolated per tab. One CPU job runs at a time. Each video is limited to 2 GB, with
20 videos / 4 GB per tab. Keep the tab open to upload and follow progress; refreshing
creates a new session. Uploaded videos, analysis, outputs and draft ZIPs are stored
in a private temporary directory (or the explicitly chosen `--data-dir`), not the
repository. A small WebM preview is also produced when ffmpeg has libvpx/libopus; the downloaded
MP4 remains the full-resolution delivery. The temporary directory is removed on orderly shutdown. Interrupted
shutdowns can leave files behind; workspace restarts do not preserve browser job
sessions. The interface is for local/private use, not a multi-user hosted service.

For your own password-protected HTTPS site, follow the
[Render account and deployment guide](deploy-render.md). The supplied Docker image
requires `RECAP_WEB_PASSWORD`; the local command also enables HTTP Basic login
when that variable is set (username `recap`). Use HTTPS when reaching it remotely.
