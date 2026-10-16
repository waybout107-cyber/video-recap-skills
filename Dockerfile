FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 recap

WORKDIR /app
COPY scripts/auto_recap.py scripts/auto_media.py scripts/auto_mimo.py scripts/auto_web.py scripts/auto_web.html ./scripts/
COPY skills/video-assemble/ ./skills/video-assemble/

# Normalize source permissions: cloud workspaces may create files as mode 0600.
RUN chmod -R a+rX /app

USER recap
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/health',timeout=4)"
CMD ["python", "scripts/auto_web.py", "--host", "0.0.0.0", "--require-auth"]
