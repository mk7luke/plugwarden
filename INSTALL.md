# PlugWarden 2.0: install

PlugWarden (formerly LGT AMP Sync) runs as a Docker Compose service. Deployment, configuration (Cloudflare Access, datastore, retention) and migration from the old systemd install are described in **[dev/DEPLOY_NOTES.md](dev/DEPLOY_NOTES.md)**.

Quick start on the host:

```bash
cp .env.example .env && chmod 600 .env      # review the values
mkdir -p data && sudo chown "$(id -u amp):$(id -g amp)" data   # state dir, owned by your AMP user
docker compose up -d --build
curl -s http://127.0.0.1:8078/healthz        # {"ok":true}
```

Point your Cloudflare Tunnel ingress for your PlugWarden hostname at `http://localhost:8078`.
