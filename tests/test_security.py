"""Security round: Cloudflare Access JWT auth, Host allowlist, CSRF, startup refusal."""
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth, config
from app.main import app
from conftest import CSRF, client_for, snapshot_tree

TEAM, AUD = "example.cloudflareaccess.com", "aud-tag-123"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(priv, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
    return {**d, "kid": kid, "alg": "RS256", "use": "sig"}


def _token(priv, kid, **over):
    now = int(time.time())
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "email": "alice@example.com", "iat": now, "exp": now + 600,
              "nbf": now - 5, **over}
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


@pytest.fixture()
def cf(env, monkeypatch):
    keys = {"k1": _key()}
    served = {"keys": [_jwk(keys["k1"], "k1")]}
    fetches = []

    def handler(req):
        fetches.append(str(req.url))
        assert str(req.url) == f"https://{TEAM}/cdn-cgi/access/certs"
        return httpx.Response(200, json=served)

    monkeypatch.setattr(auth, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "JWKS_MIN_REFRESH", 0)
    monkeypatch.setattr(config, "AUTH_MODE", "cf-access")
    monkeypatch.setattr(config, "CF_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(config, "CF_AUD", AUD)
    auth.JWKS.reset()
    yield {"keys": keys, "served": served, "fetches": fetches}
    auth.JWKS.reset()


def test_cf_access_valid_token_sets_identity(cf):
    c = client_for(app)
    assert c.get("/api/v2/health").status_code == 401  # no token
    tok = _token(cf["keys"]["k1"], "k1")
    r = c.get("/api/v2/overview", headers={auth.JWT_HEADER: tok,
                                           "Cf-Access-Authenticated-User-Email": "ceo@victim.com"})
    assert r.status_code == 200 and r.json()["user"] == "alice@example.com"
    c.cookies.set("CF_Authorization", tok)
    assert c.get("/api/v2/overview").json()["user"] == "alice@example.com"  # cookie form


@pytest.mark.parametrize("over,why", [
    ({"aud": ["other-app"]}, "aud"), ({"iss": "https://evil.cloudflareaccess.com"}, "iss"),
    ({"exp": int(time.time()) - 3600}, "exp"), ({"nbf": int(time.time()) + 3600}, "nbf"),
    ({"email": None}, "identity"),
])
def test_cf_access_rejects_bad_claims(cf, over, why):
    claims = {k: v for k, v in over.items() if v is not None}
    tok = _token(cf["keys"]["k1"], "k1", **claims)
    if over.get("email", 1) is None:
        tok = jwt.encode({k: v for k, v in jwt.decode(tok, options={"verify_signature": False}).items()
                          if k != "email"}, cf["keys"]["k1"], algorithm="RS256", headers={"kid": "k1"})
    r = client_for(app).get("/api/v2/health", headers={auth.JWT_HEADER: tok})
    assert r.status_code == 401, why


def test_cf_access_rejects_forged_and_alg_none(cf):
    forged = _token(_key(), "k1")  # right kid, wrong key
    assert client_for(app).get("/api/v2/health", headers={auth.JWT_HEADER: forged}).status_code == 401
    none_tok = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "email": "x@y", "exp": int(time.time()) + 60},
                          None, algorithm="none")
    assert client_for(app).get("/api/v2/health", headers={auth.JWT_HEADER: none_tok}).status_code == 401


def test_jwks_refreshes_on_unknown_kid(cf):
    c = client_for(app)
    assert c.get("/api/v2/health", headers={auth.JWT_HEADER: _token(cf["keys"]["k1"], "k1")}).status_code == 200
    cf["keys"]["k2"] = _key()
    cf["served"]["keys"] = [_jwk(cf["keys"]["k2"], "k2")]  # rotation
    n = len(cf["fetches"])
    assert c.get("/api/v2/health", headers={auth.JWT_HEADER: _token(cf["keys"]["k2"], "k2")}).status_code == 200
    assert len(cf["fetches"]) == n + 1
    assert c.get("/api/v2/health", headers={auth.JWT_HEADER: _token(cf["keys"]["k2"], "k2")}).status_code == 200
    assert len(cf["fetches"]) == n + 1  # cached


def test_none_mode_refuses_non_loopback_clients(env):
    from fastapi.testclient import TestClient
    r = TestClient(app, base_url="http://localhost", headers=CSRF).get("/api/v2/health")  # client "testclient"
    assert r.status_code == 403


def test_host_allowlist_and_csrf(env):
    c = client_for(app)
    assert c.get("/api/v2/health", headers={"Host": "evil.example:8095"}).status_code == 400
    # DNS rebinding: Host and Origin both the attacker's name
    r = c.post("/api/v2/updates/check", headers={"Host": "evil.example", "Origin": "http://evil.example"})
    assert r.status_code == 400
    assert c.post("/api/v2/updates/check", headers={"Origin": "null"}).status_code == 403
    assert c.post("/api/v2/updates/check", headers={"X-Requested-With": ""}).status_code == 403
    assert c.post("/api/v2/updates/check", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/v2/updates/check", headers={"Origin": "http://localhost:8095"}).status_code == 200


def test_upload_size_checked_before_parsing(env):
    c = client_for(app)
    r = c.post("/api/v2/upload", headers={"Content-Length": str(config.MAX_UPLOAD_BYTES * 2),
                                          "Content-Type": "multipart/form-data; boundary=x"}, content=b"--x--")
    assert r.status_code == 413


def test_startup_refusals(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "none")
    monkeypatch.setattr(config, "BIND", "0.0.0.0")
    with pytest.raises(RuntimeError, match="loopback"):
        auth.check_startup()
    monkeypatch.setattr(config, "BIND", "127.0.0.1")
    auth.check_startup()
    monkeypatch.setattr(config, "AUTH_MODE", "cf-access")
    monkeypatch.setattr(config, "CF_AUD", "")
    with pytest.raises(RuntimeError, match="LGT_CF_AUD"):
        auth.check_startup()


def test_security_headers_and_docs_off(env):
    r = client_for(app).get("/api/v2/health")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors 'none'" in r.headers[
        "content-security-policy"]
    assert client_for(app).get("/api/v2/docs").status_code == 404


# ---------------------------------------------------------------- H2: hostile descriptors

def _bomb_yml():
    lines = ['a0: &a0 [' + ",".join(['"xxxxxxxxxx"'] * 9) + "]"]
    for i in range(1, 9):
        lines.append(f"a{i}: &a{i} [" + ",".join([f"*a{i-1}"] * 9) + "]")
    return "\n".join(lines + ["name: *a8", "version: '1.0'", "main: x.Y"])


def _raw_jar(path, files):
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return path


def test_alias_bomb_and_deep_nesting_do_not_break_listings(env):
    import resource
    from app import inventory
    bomb = _raw_jar(env["a"] / "Bomb.jar", {"plugin.yml": _bomb_yml()})
    deep = _raw_jar(env["a"] / "Deep.jar", {"plugin.yml": "name: Deep\nversion: 1\ndepend: " + "[" * 5000 + "]" * 5000})
    deepjson = _raw_jar(env["proxy"] / "DeepV.jar", {"velocity-plugin.json": '{"id":"x","name":' + "[" * 50000 + "]" * 50000 + "}"})
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, hard))  # the old parser needed > 1 GiB for this bomb
    try:
        d = inventory.read_descriptors(bomb)
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    assert d.get("bukkit") is None or len(d["bukkit"]["name"]) < 100
    with client_for(app) as c:
        for path in ("/api/v2/overview", "/api/v2/servers", "/api/v2/matrix", "/api/v2/updates",
                     "/api/v2/servers/M1-hub01/plugins", "/api/v2/servers/M0-proxy01/plugins"):
            assert c.get(path).status_code == 200, path
        rows = {p["jar"]: p for p in c.get("/api/v2/servers/M1-hub01/plugins").json()}
    assert rows["Deep.jar"]["name"] == "Deep"  # line fallback, no recursion
    assert inventory.jar_meta(deepjson)["descriptors"] == {}


def test_descriptor_fields_only_scalars(env):
    from app import inventory
    j = _raw_jar(env["tmp"] / "x" / "W.jar", {"plugin.yml": "name: [a, b]\nversion: {x: 1}\n"})
    assert inventory.read_descriptors(j) == {}
    j = _raw_jar(env["tmp"] / "x" / "V.jar", {"plugin.yml": "name: Ok\nversion: 1.0\nauthors: [[x], y]\n"})
    d = inventory.read_descriptors(j)["bukkit"]
    assert d["name"] == "Ok" and d["version"] == "1.0" and d["authors"] == ["y"]


def test_unreadable_jar_cached_with_error(env):
    from app import inventory
    bad = env["a"] / "Broken.jar"
    bad.write_bytes(b"PK\x05\x06" + b"\x00" * 8 + (2 ** 31).to_bytes(4, "little") + b"\x00" * 6)  # huge central dir
    m = inventory.jar_meta(bad)
    assert m["valid_zip"] is False and "unreadable descriptor" in m["error"]
    with client_for(app) as c:
        row = next(p for p in c.get("/api/v2/servers/M1-hub01/plugins").json() if p["jar"] == "Broken.jar")
    assert row["descriptor_error"].startswith("unreadable descriptor") and row["valid"] is False


# ---------------------------------------------------------------- H1: backups/undo cover only what changed

def _plan_deploy(body):
    from app import actions, engine, jobs
    return jobs.wait(actions.start_deploy("t", engine.plan(body, "t")["plan_id"]), 30)


def test_undo_of_folder_push_never_touches_player_data(env):
    from app import actions, jobs
    ud = env["a"] / "Essentials" / "userdata" / "u.yml"
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"folders": ["Essentials"]}})
    assert job.status == "done"
    from app import engine
    rels = [e["rel"] for e in engine.Backup(job.id).entries]
    assert rels and not any("userdata" in r for r in rels)  # only what rsync changed/created/deleted
    ud.write_text("money: 999999\n")                        # players keep playing after the push
    (ud.parent / "newplayer.yml").write_text("money: 5\n")
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    assert undo.status == "done", undo.log
    assert ud.read_text() == "money: 999999\n" and (ud.parent / "newplayer.yml").exists()
    assert (env["a"] / "Essentials" / "config.yml").read_text() == "old-config\n"
    assert (env["a"] / "Essentials" / "stale.yml").read_text() == "stale\n"


def test_undo_skips_files_changed_since_job(env):
    from app import actions, jobs
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"paths": ["Essentials/config.yml"]}})
    cfg = env["a"] / "Essentials" / "config.yml"
    cfg.write_text("admin edited this after the push\n")
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    row = undo.results[0]
    assert row["outcome"] == "skipped" and row["reason_code"] == "changed_since_job"
    assert cfg.read_text() == "admin edited this after the push\n"


def test_undo_refuses_jar_restore_next_to_newer_version(env):
    from app import actions, jobs, inventory
    from conftest import make_jar
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                        "items": {"jars": ["CoreProtect-24.1.jar"]}})
    (env["a"] / "CoreProtect-24.1.jar").unlink()               # someone swapped in 25.0 by hand
    make_jar(env["a"] / "CoreProtect-25.0.jar", "CoreProtect", "25.0")
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    jars = sorted(p["jar"] for p in inventory.list_plugins(inventory.get_server("M1-hub01"))
                  if p["key"] == "bukkit:coreprotect")
    assert jars == ["CoreProtect-25.0.jar"]
    assert any(r["reason_code"] == "changed_since_job" for r in undo.results)


# ---------------------------------------------------------------- M6: undo re-checked under the lock

def test_undo_refused_while_busy_and_rechecked_in_job(env, monkeypatch):
    import threading
    from app import actions, engine, jobs, plans
    j1 = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                       "items": {"paths": ["Essentials/config.yml"]}})
    gate = threading.Event()
    blocker = jobs.submit("test-block", "t", {}, lambda job: gate.wait(10))
    for _ in range(50):
        if blocker.status == "running":
            break
        time.sleep(0.05)
    with pytest.raises(plans.PlanError) as e:
        actions.start_undo("t", j1.id)
    assert e.value.detail["code"] == "busy"
    # force submission past the busy check, then let a later job touch the same file before it runs
    monkeypatch.setattr(jobs, "active_jobs", lambda: [])
    undo = actions.start_undo("t", j1.id)
    later = jobs.Job("deploy", "t", {}, mutating=True)
    later.id = "29990101-000000-ffffff"
    b = engine.Backup(later.id)
    b._record({"server": "M1-hub01", "rel": "Essentials/config.yml", "existed": True, "type": "file", "store": "x"})
    later.save()
    gate.set()
    jobs.wait(undo, 30)
    assert undo.status == "failed" and "later job" in undo.results[0]["detail"]
    assert (env["a"] / "Essentials" / "config.yml").read_text() == "new-config\n"


# ---------------------------------------------------------------- M2/M3: audit + source override flag

def test_audit_log_records_changes_and_plan_value_reads(env):
    from conftest import make_jar
    with client_for(app) as c:
        c.post("/api/v2/plugins/bukkit:vault/pin", json={"version": "1.7.0", "servers": ["M1-hub01"]})
        c.post("/api/v2/plugins/bukkit:vault/ignore", json={"ignored": True})
        c.put("/api/v2/settings", json={"source_map": {"bukkit:vault": {"kind": "spiget", "id": "34315"}}})
        jar = make_jar(env["tmp"] / "u" / "Vault-2.jar", "Vault", "2")
        c.post("/api/v2/upload", files={"file": ("Vault-2.jar", jar.read_bytes())})
        (env["src"] / "LP").mkdir()
        (env["a"] / "LP").mkdir()
        (env["src"] / "LP" / "config.yml").write_text("server: survival\n")
        (env["a"] / "LP" / "config.yml").write_text("server: hub\n")
        c.post("/api/v2/deploy/plan", json={"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                                           "items": {"paths": ["LP/config.yml"]}})
        log = c.get("/api/v2/access-log").json()["entries"]
    by = {e["action"]: e for e in log}
    assert by["pin"]["before"] is None and by["pin"]["after"] == {"version": "1.7.0", "servers": ["M1-hub01"]}
    assert by["ignore"]["after"] == {"servers": "*"}
    assert by["settings"]["after"]["source_map"]["bukkit:vault"]["kind"] == "spiget"
    assert by["upload"]["path"] == "Vault-2.jar" and "sha1" in by["upload"]["detail"]
    assert by["plan-values"]["path"] == "LP/config.yml" and by["plan-values"]["servers"] == ["M1-hub01"]


def test_plan_warning_values_redacted(env):
    from app import engine
    (env["src"] / "LP").mkdir()
    (env["a"] / "LP").mkdir()
    (env["src"] / "LP" / "config.yml").write_text("server: survival\nuri: 'mongodb://admin:hunter2@db'\n")
    (env["a"] / "LP" / "config.yml").write_text("server: hub\nuri: 'mongodb://admin:s3cret@db2'\n")
    make = __import__("conftest").make_server
    p = engine.dry_run({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                        "items": {"paths": ["LP/config.yml"]}})
    text = json.dumps(p["warnings"])
    assert "hunter2" not in text and "s3cret" not in text


# ---------------------------------------------------------------- M1: redaction shapes

def test_redaction_covers_blocks_numbers_inline_json_lists_and_tokens():
    from app.redact import redact_lines
    lines = ["password: |\n", "  hunter2-block\n", "pass: hunter2\n", "pw: 12345678\n", "auth: abc\n",
             "credentials: user:pw\n", '{"password": "hunter2", "user": "x"}\n', "passwords:\n", "  - listsecret\n",
             "db-uri: 'mongodb://admin:hunter2@db'\n", "bot-token: >-\n", "  folded.secret\n",
             "access: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n", "enabled: true\n", "author: Luck\n"]
    out, found = redact_lines(lines, "c.yml")
    text = "".join(out)
    for leak in ("hunter2", "12345678", "abc", "user:pw", "listsecret", "folded.secret", "ghp_"):
        assert leak not in text, leak
    assert "enabled: true" in text and "author: Luck" in text
    js, _ = redact_lines(['{"a": {"apiKey": "k1", "n": 1}, "list": ["sk_live_abcdefghijk"]}'], "x.json")
    assert "k1" not in "".join(js) and "sk_live" not in "".join(js) and '"n": 1' in "".join(js)


# ---------------------------------------------------------------- M5: retention + free space

def test_backup_retention_by_age_and_size(env):
    import os
    from app import engine, config
    root = config.state("backups")
    for i, (age_days, size) in enumerate([(0, 10), (1, 10), (40, 10), (2, 3000)]):
        d = root / f"2026010{i}-000000-00000{i}"
        d.mkdir(parents=True)
        (d / "f").write_bytes(b"x" * size)
        t = time.time() - age_days * 86400
        os.utime(d, (t, t))
    pruned = engine.prune_backups(100, max_age_days=30, max_bytes=2000)
    assert "20260102-000000-000002" in pruned  # too old
    assert "20260103-000000-000003" not in pruned  # newest kept even though large (it is first)
    assert "20260101-000000-000001" in pruned or "20260100-000000-000000" in pruned  # size cap hit


def test_jobs_refused_when_disk_nearly_full(env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MIN_FREE_BYTES", 10 ** 18)
    with client_for(app) as c:
        pid = c.post("/api/v2/deploy/plan", json={"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync",
                                                  "items": {"jars": ["Vault.jar"]}}).json()["plan_id"]
        r = c.post("/api/v2/deploy", json={"plan_id": pid})
    assert r.status_code == 507 and r.json()["code"] == "insufficient_space"


def test_housekeeping_prunes_temp_and_job_records(env, monkeypatch):
    import os
    from app import actions, config, jobs
    old = config.state("uploads", "abc")
    old.mkdir(parents=True)
    t = time.time() - 2 * 86400
    os.utime(old, (t, t))
    for i in range(5):
        (config.state("jobs") / f"2020010{i}-000000-00000{i}.json").write_text("{}")
    monkeypatch.setattr(actions, "JOB_RECORDS_KEEP", 2)
    actions.housekeeping()
    assert not old.exists() and len(list(config.state("jobs").glob("*.json"))) == 2


# ---------------------------------------------------------------- LOW items

def test_download_host_allowlist_and_hash_validation(env):
    from app import updates
    seen = []
    updates.TRANSPORT = httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200, content=b"x"))[1])
    with updates._client() as c:
        with pytest.raises(httpx.UnsupportedProtocol):
            updates._download(c, {"download_url": "https://192.168.1.10/admin", "hashes": {}}, "x", print)
        with pytest.raises(ValueError, match="invalid file hash"):
            updates._download(c, {"download_url": "https://cdn.modrinth.com/a.jar", "verified": True,
                                  "hashes": {"sha512": "../../../../opt/x"}}, "x", print)
        with pytest.raises(ValueError, match="size mismatch"):
            updates._download(c, {"download_url": "https://cdn.modrinth.com/a.jar", "size": 999, "hashes": {}},
                              "x", print)
    assert seen == ["https://cdn.modrinth.com/a.jar"]
    from app import config
    assert not any(p.name == "opt" for p in config.STATE_DIR.rglob("*"))


def test_control_characters_rejected(env):
    from app import inventory
    with pytest.raises(inventory.PathError):
        inventory.check_rel("a\n*deleting Essentials/")
    (env["a"] / "evil\nname.yml").write_text("x")
    tree = inventory.list_tree(inventory.get_server("M1-hub01"), "")
    assert not any("\n" in e["name"] for e in tree["entries"])


# ---------------------------------------------------------------- follow-up review items

def test_zip_entry_cap_includes_zip64(env, monkeypatch):
    import zipfile
    from app import inventory
    monkeypatch.setattr(inventory, "MAX_ZIP_ENTRIES", 10)
    p = env["tmp"] / "many.jar"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("plugin.yml", "name: X\nversion: 1\n")
        for i in range(20):
            z.writestr(str(i), b"")
    with pytest.raises(zipfile.BadZipFile, match="too many"):
        inventory._check_central_directory(p)


def test_none_mode_refuses_public_hostname_and_proxied_requests(env, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "none")
    monkeypatch.setattr(config, "HOSTNAME", "amp.example.com")
    with pytest.raises(RuntimeError, match="public deployment"):
        auth.check_startup()
    monkeypatch.setattr(config, "HOSTNAME", "")
    c = client_for(app)
    assert c.get("/api/v2/health", headers={"Cf-Connecting-IP": "203.0.113.9"}).status_code == 403
    assert c.get("/api/v2/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_jwks_outage_keeps_cached_keys(cf, monkeypatch):
    c = client_for(app)
    tok = _token(cf["keys"]["k1"], "k1")
    assert c.get("/api/v2/health", headers={auth.JWT_HEADER: tok}).status_code == 200
    monkeypatch.setattr(auth, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(503)))
    auth.JWKS.fetched = 0  # TTL expired
    assert c.get("/api/v2/health", headers={auth.JWT_HEADER: tok}).status_code == 200


def test_index_has_strict_csp_with_inline_hashes(env):
    r = client_for(app).get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self' 'sha256-" in csp and "unsafe-inline" not in csp.split(
        "style-src")[0]


def test_undo_handles_symlinks_and_utf8_names(env):
    from app import actions, jobs
    src, dst = env["src"] / "Ess", env["a"] / "Ess"
    src.mkdir()
    dst.mkdir()
    (src / "config.yml").write_text("new\n")
    (dst / "config.yml").write_text("old\n")
    (src / "café.yml").write_text("x\n")
    (src / "lnk").symlink_to("config.yml")
    before = snapshot_tree_links(env["a"])
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"folders": ["Ess"]}})
    assert (dst / "café.yml").exists() and (dst / "lnk").is_symlink()
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    assert undo.status == "done", [r for r in undo.results]
    assert snapshot_tree_links(env["a"]) == before


def snapshot_tree_links(root):
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = ("->" + str(p.readlink())) if p.is_symlink() else (p.read_bytes() if p.is_file() else "<dir>")
    return out


def test_retention_and_record_pruning_protect_undo_state(env):
    from app import actions, config, engine, jobs
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "sync", "items": {"jars": ["Vault.jar"]}})
    for i in range(3):  # newer, backup-less records
        (config.state("jobs") / f"2999010{i}-000000-00000{i}.json").write_text("{}")
    assert jobs.prune_records(1) == 2  # the job with a backup stays even though it is beyond the limit
    assert (config.state("jobs") / f"{job.id}.json").exists()
    actions._undo_pending.add(job.id)
    try:
        engine_pruned = actions.housekeeping()["backups"]
        assert job.id not in engine_pruned
    finally:
        actions._undo_pending.discard(job.id)


def test_scheduler_apply_cycle_end_to_end(env):
    from app import scheduler, settings, updates, inventory
    from conftest import make_jar
    from test_engine import _mock_modrinth
    new_bytes = make_jar(env["tmp"] / "dl" / "x.jar", "CoreProtect", "24.1", extra=b"mr").read_bytes()
    (env["src"] / "CoreProtect-24.1.jar").write_bytes(new_bytes)  # canary runs exactly that build
    _mock_modrinth(env, new_bytes)
    settings.update({"auto_update": {"mode": "apply", "min_release_age_hours": 0, "canary_soak_hours": 0,
                                     "canary_server": "elChapo01"}})
    from datetime import datetime, timedelta
    from conftest import write_log
    logs = env["src"].parent / "logs"
    before = datetime.now() - timedelta(days=1)
    write_log(logs, before, ["[x] [Server thread/INFO]: [CoreProtect] Enabling CoreProtect v23.4",
                             "[x] Done (5.1s)!"], name=before.strftime("%Y-%m-%d-2.log.gz"))
    # the canary restarted after the scheduler first saw 24.1 on it, and enabled it cleanly
    write_log(logs, datetime.now() + timedelta(seconds=60),
              [datetime.now().strftime("[%H:%M:%S]") + " [Server thread/INFO]: [CoreProtect] Enabling CoreProtect v24.1",
               datetime.now().strftime("[%H:%M:%S]") + " [Server thread/INFO]: Done (5.1s)! For help, type \"help\""])
    result = scheduler.run_cycle()
    assert "apply: done" in result, result
    jars = [p["jar"] for p in inventory.list_plugins(inventory.get_server("M1-hub01")) if p["key"] == "bukkit:coreprotect"]
    assert jars == ["CoreProtect-CE-24.1.jar"]
    sel = scheduler.status()["last_selection"]
    row = sel["applied"][0]
    assert (row["server"], row["key"], row["to_version"]) == ("M1-hub01", "bukkit:coreprotect", "24.1")
    assert row["canary_health"]["status"] == "healthy" and "Enabling CoreProtect v24.1" in row["canary_health"]["excerpt"][0]


def test_healthz_and_insecure_container_mode(env, monkeypatch):
    from fastapi.testclient import TestClient
    raw = TestClient(app)  # non-loopback client, no headers
    assert raw.get("/healthz").json() == {"ok": True}
    monkeypatch.setattr(config, "BIND", "0.0.0.0")
    monkeypatch.setattr(config, "AUTH_MODE", "none")
    with pytest.raises(RuntimeError):
        auth.check_startup()
    monkeypatch.setattr(config, "ALLOW_INSECURE", True)
    auth.check_startup()
    r = TestClient(app, base_url="http://localhost").get("/api/v2/health")  # docker-gateway-like client
    assert r.status_code == 200


def test_move_backup_survives_cross_mount_rename(env, monkeypatch):
    import errno as _errno
    import os as _os
    from app import actions, jobs, inventory
    real_rename = _os.rename

    def exdev(a, b):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")
    job = _plan_deploy({"source": "elChapo01", "targets": ["M1-hub01"], "action": "replace",
                        "items": {"jars": ["CoreProtect-24.1.jar"]}})
    monkeypatch.setattr(_os, "rename", exdev)
    undo = jobs.wait(actions.start_undo("t", job.id), 30)
    monkeypatch.setattr(_os, "rename", real_rename)
    assert undo.status == "done", undo.results
    jars = [p["jar"] for p in inventory.list_plugins(inventory.get_server("M1-hub01")) if p["key"] == "bukkit:coreprotect"]
    assert jars == ["CoreProtect-23.1.jar"]
