# Publish the recap app on Render from your phone

## 1. Create your account

1. Open [Render sign-up](https://dashboard.render.com/register) on Android Chrome.
2. Use GitHub sign-in if offered, with the account that owns this repository.
   Follow Render's email/verification steps. Keep login codes in the sign-in flow.
3. If prompted to create a workspace, use a personal workspace for your own app.
4. Stop at the dashboard. Creating an account and deploying a service are separate
   steps; review any pricing/payment screen yourself before accepting it.

## 2. Publish these deployment files to GitHub

The files created in this coding task must be on GitHub before Render can see them.
Use this task's **Commit and push** control to publish the prepared patch to
`coderabbit/video-recap-generator/dc0baa99` in
`waybout107-cyber/video-recap-skills`. This guide does not assume that happened.

The Blueprint explicitly targets that branch. If you later merge to `main`, change
its `branch` value to `main` too. No pull request or release tag is required for a
branch-based deployment.

## 3. Create the app

1. In the [Render dashboard](https://dashboard.render.com/), choose **New → Blueprint**.
2. Connect GitHub if prompted, granting access to this repository. Select
   `waybout107-cyber/video-recap-skills` and the branch above. The Blueprint file
   is `render.yaml` in the repository root.
3. When asked for `RECAP_WEB_PASSWORD`, choose a unique **website password of at
   least 12 characters**. This is your app login password, not your MiMo API key.
4. Review the proposed **Free** web service before deploying. The supplied Blueprint
   uses Free for a small demo; do not choose a paid instance unless you accept its
   displayed costs. If your account cannot create Free services, stop and review
   the available plans rather than substituting one automatically.
5. Deploy and wait for the status to become **Live**. Open the service's HTTPS
   `onrender.com` URL.
6. Your browser asks for a username and password: username **recap**, password the
   value you set for `RECAP_WEB_PASSWORD`. Every app page and download requires it;
   `/health` is public and returns only `{"status":"ok"}`.
7. Tap **Try an 8-second offline demo** first. If it finishes, paste your MiMo key
   in the app form, upload a short test video and create a short narrated recap.

For manual service creation instead of Blueprint: choose a **Web Service**, this
repository and branch, runtime **Docker**, Dockerfile path **./Dockerfile**, and add
`RECAP_WEB_PASSWORD` under Environment. Keep the Docker command empty (the image
has one), set health check `/health`, and review the instance plan before creation.

## What is included

The Docker image installs Python 3.12, ffmpeg/ffprobe with H.264/AAC support and CJK
fonts. No GPU, pip dependencies, database or static-site service is needed.
The server binds to `0.0.0.0` and reads Render's `PORT`. It runs as a non-root user.
A website password is required by the image before it starts. Render terminates
HTTPS; do not expose the plain HTTP container directly to the public Internet.
Your MiMo key remains a single-run form input; it is not part of the image or
Render configuration. The site is intended for you or a small trusted group with
the shared password, not as a public upload service for strangers.

## Trial and storage limits

Free services [spin down after inactivity and can restart at any time](https://render.com/docs/free).
A long render can exhaust a small instance's CPU/memory. Keep the app tab open and
start with the offline demo, then a short source. Reliable long-video production
may require a paid instance with enough CPU/RAM; inspect Render's current plans
before deciding. MiMo API usage is billed separately to your key.

Uploaded sources, outputs and job/session state are temporary. They are lost on a
restart, redeploy or spin-down. **Download finished MP4s, SRTs and drafts right away.**
Automatic deploys are disabled in this Blueprint so a push does not unexpectedly
interrupt a job. Do not deploy changes while a recap is running.

A custom domain is optional: the generated HTTPS URL works on Android. If the app
stops loading, open its Render service dashboard and inspect the latest deploy and
runtime logs. A startup message about `RECAP_WEB_PASSWORD` means the website
password is missing or too short; set it in Environment and deploy again.

Sources: [Docker deployment](https://render.com/docs/docker),
[Blueprint specification](https://render.com/docs/blueprint-spec),
[Free service limits](https://render.com/docs/free).
