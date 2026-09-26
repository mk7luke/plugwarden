# Luke’s Genius Tools — AMP Sync (GUI) Install

This deploys a web GUI around your existing `/usr/local/bin/amp-plugin-sync` (**unchanged**).
It runs internally on **port 8078** and is meant to be exposed via your existing Cloudflare Tunnel + Access.

## Install

### 1) Copy app to /opt
```bash
sudo mkdir -p /opt/lgt-amp-sync
sudo rsync -a ./lgt_amp_sync/ /opt/lgt-amp-sync/
```

### 2) Python venv + deps
```bash
sudo -u amp bash -lc '
cd /opt/lgt-amp-sync
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
'
```

### 3) State dirs (logs + uploads)
```bash
sudo mkdir -p /var/lib/lgt-amp-sync/{uploads,jobs}
sudo chown -R amp:amp /var/lib/lgt-amp-sync
```

### 4) systemd service
```bash
sudo cp /opt/lgt-amp-sync/lgt-amp-sync.service /etc/systemd/system/lgt-amp-sync.service
sudo systemctl daemon-reload
sudo systemctl enable --now lgt-amp-sync
sudo systemctl status lgt-amp-sync --no-pager
```

### 5) Confirm listening
```bash
sudo ss -lntp | grep 8078
```

### 6) Cloudflare Tunnel
Point your tunnel at:
- `http://127.0.0.1:8078`

Then add Cloudflare Access policy for you + co-owner.

## Where logs live
- `/var/lib/lgt-amp-sync/jobs/*.log`
- `/var/lib/lgt-amp-sync/uploads/*`

## How “choose targets” works (important)
Your script supports `--exclude`, not `--include`.  
So the GUI computes the exclude list automatically (everything you didn’t select), then runs the script with `--yes`.

## Security notes
- This UI assumes Cloudflare Access is your auth layer.
- For extra safety you can pin the base path by uncommenting `LGT_BASE_OVERRIDE` in the systemd unit.
