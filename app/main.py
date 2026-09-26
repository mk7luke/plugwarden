from __future__ import annotations

import os, re, shlex, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_TITLE = "Luke’s Genius Tools — AMP Sync"

BASE_DEFAULT = os.environ.get("LGT_BASE", "/mnt/storage_ssd/ssd-live")
REL_PLUGINS = "Minecraft/plugins"
SCRIPT_ORIG = os.environ.get("LGT_SCRIPT", "/usr/local/bin/amp-plugin-sync")

STATE_DIR = Path(os.environ.get("LGT_STATE_DIR", "/var/lib/lgt-amp-sync"))
UPLOAD_DIR = STATE_DIR / "uploads"
JOB_DIR = STATE_DIR / "jobs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SOURCES = [s.strip() for s in os.environ.get("LGT_ALLOWED_SOURCES", "").split(",") if s.strip()]

app = FastAPI(title=APP_TITLE)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

def _now_id() -> str:
    return f"{int(time.time())}-{os.getpid()}"

def _is_instance_dir(base: Path, name: str) -> bool:
    return (base / name / REL_PLUGINS).is_dir()

def discover_instances(base: str) -> List[str]:
    b = Path(base)
    if not b.is_dir():
        return []
    out: List[str] = []
    for child in sorted(b.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and _is_instance_dir(b, child.name):
            out.append(child.name)
    return out

def list_plugins(base: str, instance: str) -> Dict[str, List[str]]:
    b = Path(base)
    plugins_dir = b / instance / REL_PLUGINS
    if not plugins_dir.is_dir():
        raise HTTPException(status_code=404, detail="plugins dir not found")
    jars, folders, files = [], [], []
    for p in sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower()):
        if p.is_dir():
            folders.append(p.name)
        elif p.is_file():
            (jars if p.suffix.lower()==".jar" else files).append(p.name)
    return {"jars": jars, "folders": folders, "files": files}

def ensure_script_exists():
    if not Path(SCRIPT_ORIG).is_file():
        raise HTTPException(status_code=500, detail=f"Missing script: {SCRIPT_ORIG}")

def build_temp_script(source_instance: str) -> Path:
    ensure_script_exists()
    if ALLOWED_SOURCES and source_instance not in ALLOWED_SOURCES:
        raise HTTPException(status_code=403, detail="Source instance not allowed by server policy.")
    src = Path(SCRIPT_ORIG).read_text(encoding="utf-8", errors="replace")
    new = re.sub(r'(?m)^SOURCE_INSTANCE="[^"]*"\s*$', f'SOURCE_INSTANCE="{source_instance}"', src)
    base_override = os.environ.get("LGT_BASE_OVERRIDE", "").strip()
    if base_override:
        new = re.sub(r'(?m)^BASE="[^"]*"\s*$', f'BASE="{base_override}"', new)
    fd, tmp_path = tempfile.mkstemp(prefix="amp-plugin-sync-", suffix=".sh")
    os.close(fd)
    p = Path(tmp_path)
    p.write_text(new, encoding="utf-8")
    p.chmod(0o755)
    return p

def compute_excludes(all_instances: List[str], selected_targets: List[str], source: str, proxy: str="M0-proxy01") -> str:
    sel = set(selected_targets)
    excludes = []
    for inst in all_instances:
        if inst == source:
            continue
        if inst not in sel:
            excludes.append(inst)
    excludes.append(proxy)
    return ",".join(sorted(set(excludes), key=lambda s: s.lower()))

def _run_command(cmd: List[str], job_id: str) -> Dict[str, Any]:
    log_path = JOB_DIR / f"{job_id}.log"
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dur = time.time() - start
    log = []
    log.append(f"$ {' '.join(shlex.quote(c) for c in cmd)}\n\n")
    if proc.stdout:
        log.append(proc.stdout)
    if proc.stderr:
        log.append("\n[stderr]\n")
        log.append(proc.stderr)
    log_path.write_text("".join(log), encoding="utf-8")
    return {"job_id": job_id, "returncode": proc.returncode, "seconds": round(dur, 3)}

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": APP_TITLE})

@app.get("/api/instances")
def api_instances(base: str = BASE_DEFAULT):
    inst = discover_instances(base)
    return {"base": base, "instances": inst}

@app.get("/api/source_listing")
def api_source_listing(source: str, base: str = BASE_DEFAULT):
    return {"source": source, "base": base, **list_plugins(base, source)}

@app.get("/api/folder_contents")
def api_folder_contents(folder: str, source: str, base: str = BASE_DEFAULT):
    """List files within a specific plugin folder for surgical sync"""
    base_p = Path(base)
    folder_path = base_p / source / REL_PLUGINS / folder

    if not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    files = []
    for item in sorted(folder_path.iterdir(), key=lambda p: p.name.lower()):
        if item.is_file():
            files.append(item.name)

    return {"folder": folder, "source": source, "files": files}

@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    p = JOB_DIR / f"{job_id}.log"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "log": p.read_text(encoding="utf-8", errors="replace")}

def _split_list(s: str) -> List[str]:
    out = []
    for part in re.split(r'[\n,]+', (s or "").strip()):
        part = part.strip()
        if part:
            out.append(part)
    return out

@app.post("/api/run")
async def api_run(
    base: str = Form(BASE_DEFAULT),
    source: str = Form(...),
    targets: str = Form(...),
    mode: str = Form("sync"),
    install: bool = Form(False),
    dry_run: bool = Form(False),
    backup: bool = Form(False),
    plugins: str = Form(""),
    folders: str = Form(""),
    paths: str = Form(""),
):
    base_p = Path(base)
    if not base_p.is_dir():
        raise HTTPException(status_code=400, detail="BASE not found on server.")
    all_instances = discover_instances(base)
    # For delete operations, source is set to proxy (M0-proxy01) as a workaround, so skip validation
    if mode != "delete" and source not in all_instances:
        raise HTTPException(status_code=400, detail="Invalid source instance.")
    target_list = [t.strip() for t in targets.split(",") if t.strip()]
    if not target_list:
        raise HTTPException(status_code=400, detail="Pick at least one target.")
    for t in target_list:
        if t not in all_instances:
            raise HTTPException(status_code=400, detail=f"Invalid target instance: {t}")
        # For delete operations, source is set to first target, so skip this check
        if mode != "delete" and t == source:
            raise HTTPException(status_code=400, detail="Target list cannot include source instance.")
    plugins_list = _split_list(plugins)
    folders_list = _split_list(folders)
    paths_list = _split_list(paths)
    if not (plugins_list or folders_list or paths_list):
        raise HTTPException(status_code=400, detail="Specify at least one plugin/folder/path.")
    temp_script = build_temp_script(source)
    job_id = _now_id()
    excludes_csv = compute_excludes(all_instances, target_list, source)
    cmd = [str(temp_script)]
    if mode == "delete":
        cmd.append("--delete")
    if install and mode != "delete":
        cmd.append("--install")
    if dry_run:
        cmd.append("--dry-run")
    if backup and mode != "delete":
        cmd.append("--backup")
    if excludes_csv:
        cmd += ["--exclude", excludes_csv]
    cmd.append("--yes")
    for p in plugins_list: cmd += ["--plugin", p]
    for f in folders_list: cmd += ["--folder", f]
    for pa in paths_list: cmd += ["--path", pa]
    result = _run_command(cmd, job_id)
    try: temp_script.unlink(missing_ok=True)
    except Exception: pass
    result.update({"base": base, "source": source, "targets": target_list, "excludes": excludes_csv})
    return JSONResponse(result)

@app.post("/api/upload_jar")
async def api_upload_jar(
    base: str = Form(BASE_DEFAULT),
    source: str = Form(...),
    targets: str = Form(...),
    install: bool = Form(True),
    dry_run: bool = Form(False),
    backup: bool = Form(False),
    replace_mode: str = Form("none"),
    replace_glob: str = Form(""),
    jar: UploadFile = File(...),
):
    base_p = Path(base)
    if not base_p.is_dir():
        raise HTTPException(status_code=400, detail="BASE not found.")
    all_instances = discover_instances(base)
    if source not in all_instances:
        raise HTTPException(status_code=400, detail="Invalid source instance.")
    target_list = [t.strip() for t in targets.split(",") if t.strip()]
    if not target_list:
        raise HTTPException(status_code=400, detail="Pick at least one target.")
    for t in target_list:
        if t not in all_instances:
            raise HTTPException(status_code=400, detail=f"Invalid target instance: {t}")
        if t == source:
            raise HTTPException(status_code=400, detail="Targets cannot include source.")
    filename = Path(jar.filename or "").name
    if not filename.lower().endswith(".jar"):
        raise HTTPException(status_code=400, detail="Upload must be a .jar file.")
    up_id = _now_id()
    tmp_store = UPLOAD_DIR / f"{up_id}-{filename}"
    with tmp_store.open("wb") as f:
        while True:
            chunk = await jar.read(1024*1024)
            if not chunk: break
            f.write(chunk)
    src_plugins = base_p / source / REL_PLUGINS
    if not src_plugins.is_dir():
        raise HTTPException(status_code=500, detail="Source plugins dir missing.")
    dest_jar = src_plugins / filename
    if not dry_run:
        shutil.copy2(tmp_store, dest_jar)

    delete_list: List[str] = []
    if replace_mode == "glob":
        g = replace_glob.strip()
        if not g.lower().endswith(".jar") or ("*" not in g and "?" not in g):
            raise HTTPException(status_code=400, detail="replace_glob should look like Something-*.jar")
        pat = re.compile("^" + re.escape(g).replace(r"\*", ".*").replace(r"\?", ".") + "$", re.IGNORECASE)
        for inst in target_list:
            pdir = base_p / inst / REL_PLUGINS
            if not pdir.is_dir(): continue
            for f in pdir.iterdir():
                if f.is_file() and f.suffix.lower()==".jar" and pat.match(f.name):
                    delete_list.append(f.name)
        delete_list = sorted(set(delete_list), key=lambda s: s.lower())

    excludes_csv = compute_excludes(all_instances, target_list, source)
    delete_job = None
    if delete_list:
        temp_script = build_temp_script(source)
        cmd_del = [str(temp_script), "--delete", "--yes"]
        if dry_run: cmd_del.append("--dry-run")
        if excludes_csv: cmd_del += ["--exclude", excludes_csv]
        for name in delete_list: cmd_del += ["--plugin", name]
        delete_job = _run_command(cmd_del, up_id + "-del")
        try: temp_script.unlink(missing_ok=True)
        except Exception: pass

    temp_script2 = build_temp_script(source)
    cmd_sync = [str(temp_script2), "--yes", "--plugin", filename]
    if install: cmd_sync.append("--install")
    if dry_run: cmd_sync.append("--dry-run")
    if backup: cmd_sync.append("--backup")
    if excludes_csv: cmd_sync += ["--exclude", excludes_csv]
    sync_job = _run_command(cmd_sync, up_id + "-sync")
    try: temp_script2.unlink(missing_ok=True)
    except Exception: pass

    return JSONResponse({
        "uploaded_as": filename,
        "source_written": str(dest_jar),
        "delete_candidates": delete_list,
        "delete_job": delete_job,
        "sync_job": sync_job,
    })
