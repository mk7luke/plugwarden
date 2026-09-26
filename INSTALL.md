# LGT AMP Sync 2.0: install

AMP Sync runs as a Docker Compose service. Deployment, configuration (Cloudflare Access, datastore, retention) and migration from the old systemd install are described in **[dev/DEPLOY_NOTES.md](dev/DEPLOY_NOTES.md)**.

Quick start on the host:

```bash
cp .env.example .env && chmod 600 .env      # review the values
mkdir -p data && sudo chown 1001:1001 data   # state dir, owned by amp
docker compose up -d --build
curl -s http://127.0.0.1:8078/healthz        # {"ok":true}
```

The Cloudflare Tunnel ingress for `bulkupdate.obliv.us` points at `http://localhost:8078`.
