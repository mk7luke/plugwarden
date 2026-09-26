# LGT AMP Sync 2.0 — see dev/DEPLOY_NOTES.md
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LGT_STATE_DIR=/var/lib/lgt-amp-sync \
    LGT_BIND=0.0.0.0

# rsync does every file transfer; cp (coreutils) is used for backups.
RUN apt-get update \
 && apt-get install -y --no-install-recommends rsync \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1001 amp \
 && useradd --uid 1001 --gid 1001 --no-create-home --shell /usr/sbin/nologin amp \
 && mkdir -p /var/lib/lgt-amp-sync \
 && chown 1001:1001 /var/lib/lgt-amp-sync

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip setuptools \
 && pip install -r requirements.txt
COPY app/ app/

USER 1001:1001
EXPOSE 8078

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8078/healthz', timeout=4).status == 200 else 1)"

# 0.0.0.0 inside the container; compose publishes it on the host's loopback only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8078", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
