"""Secret redaction for config text shown in the UI (diffs, plan warnings).

One implementation for every place config values leave the server:
  * keys whose name looks secret (password, pass, pwd, token, api-key, auth, credential, dsn, …): the
    value is replaced, including numbers, block scalars (`|`, `>-`), and indented lists/maps below it;
  * inline JSON pairs anywhere on a line (`{"password": "x"}`);
  * well-known token formats and URL credentials anywhere, whatever the key is called;
  * .json files are redacted through a real parse, then re-serialised.
Booleans/empty values stay visible so toggles like `BlockWebhooks: false` still diff.
"""
from __future__ import annotations

import json
import re

REDACTED = "«redacted»"

_SECRET_WORDS = re.compile(
    r"(password|passwd|passphrase|secret|token|api[-_]?key|private[-_]?key|webhook|jdbc|dsn|license|credential)",
    re.I)
_SECRET_TAIL = re.compile(r"(^|[-_.])(pass|pwd|pw|auth|key|creds?)$", re.I)
_CAMEL_KEY = re.compile(r"[a-z](Key|Pass|Pwd|Auth)$")
_AUTH_PREFIX = re.compile(r"^auth([-_.]|$|entication|orization)", re.I)

_TOKEN_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[MNO][A-Za-z\d_-]{23,}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}"),  # Discord bot token
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"https?://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I),
    re.compile(r"\bjdbc:[^\s'\"]+", re.I),
]
_URL_CREDS = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s/:@'\"]+:[^\s/@'\"]+@", re.I)

_TOGGLES = {"true", "false", "yes", "no", "on", "off", "null", "~", ""}
_BLOCK_OPENERS = {"", "|", ">", "|-", ">-", "|+", ">+", "[", "{"}
_KEY_LINE = re.compile(r"^(?P<pre>\s*(?:-\s+)?)(?P<q>[\"']?)(?P<key>[^\s\"'#:=][^\"'#:=]*?)(?P=q)"
                       r"(?P<sep>\s*[:=]\s*)(?P<val>.*?)(?P<post>\s*,?\s*(?:#.*)?)$")
_INLINE_JSON = re.compile(r"(?P<k>\"(?P<key>[^\"]+)\"\s*:\s*)(?P<v>\"(?:[^\"\\]|\\.)*\"|-?\d[\d.eE+-]*)")


def is_secret_key(key: str) -> bool:
    name = key.split(".")[-1].strip().strip("'\"")
    return bool(_SECRET_WORDS.search(name) or _SECRET_TAIL.search(name) or _CAMEL_KEY.search(name)
                or _AUTH_PREFIX.search(name))


def has_secret_value(value: str) -> bool:
    return bool(_URL_CREDS.search(value) or any(p.search(value) for p in _TOKEN_PATTERNS))


def scrub_value(value: str) -> str:
    """Redact token-shaped substrings and URL credentials inside any value."""
    for p in _TOKEN_PATTERNS:
        value = p.sub(REDACTED, value)
    return _URL_CREDS.sub(lambda m: m.group("scheme") + REDACTED + "@", value)


def shown(key: str, value: str | None) -> str | None:
    """A config value as it may be displayed (plan warnings etc.)."""
    if value is None:
        return None
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    if is_secret_key(key) and v.lower() not in _TOGGLES:
        return REDACTED
    return scrub_value(v)


def redact_lines(lines: list[str], rel: str = "") -> tuple[list[str], dict[str, str]]:
    """Returns (redacted lines, {label: original value}); labels repeat as 'key (2)' in file order."""
    if rel.lower().endswith(".json"):
        try:
            return _redact_json("".join(lines))
        except (ValueError, RecursionError):
            pass
    out: list[str] = []
    found: dict[str, str] = {}
    seen: dict[str, int] = {}
    block: tuple[int, str] | None = None  # (indent of the secret key, label) while inside its value

    def label_for(key: str) -> str:
        seen[key] = seen.get(key, 0) + 1
        return key if seen[key] == 1 else f"{key} ({seen[key]})"

    for ln in lines:
        body, nl = (ln[:-1], "\n") if ln.endswith("\n") else (ln, "")
        if body.endswith("\r"):
            body, nl = body[:-1], "\r" + nl
        indent = len(body) - len(body.lstrip(" \t"))
        if block is not None:
            if not body.strip() or indent > block[0] or (body.lstrip().startswith("- ") and indent >= block[0]):
                if body.strip():
                    found[block[1]] = found.get(block[1], "") + body.strip() + "\n"
                    out.append(body[:indent] + REDACTED + nl)
                else:
                    out.append(ln)
                continue
            block = None
        m = _KEY_LINE.match(body)
        if m and is_secret_key(m.group("key")):
            val = m.group("val").strip()
            if val.lower() in _TOGGLES - {""}:
                out.append(ln)
                continue
            label = label_for(m.group("key").strip())
            if val in _BLOCK_OPENERS or (val[:1] in "[{" and not _balanced(val)):
                block = (indent, label)
                found[label] = val + "\n"
                out.append(ln)  # the opener carries no secret; the lines below are redacted
                continue
            found[label] = val
            out.append(f"{m.group('pre')}{m.group('q')}{m.group('key')}{m.group('q')}{m.group('sep')}"
                       f"{REDACTED}{m.group('post')}{nl}")
            continue

        def inline(mm: re.Match) -> str:
            if is_secret_key(mm.group("key")):
                found[label_for(mm.group("key"))] = mm.group("v")
                return mm.group("k") + json.dumps(REDACTED, ensure_ascii=False)
            return mm.group(0)

        new = _INLINE_JSON.sub(inline, body)
        scrubbed = scrub_value(new)
        if scrubbed != new:
            key = m.group("key").strip() if m else "value"
            found[label_for(key)] = body.strip()
        out.append(scrubbed + nl)
    return out, found


def _balanced(v: str) -> bool:
    return v.count("[") == v.count("]") and v.count("{") == v.count("}")


def _redact_json(text: str) -> tuple[list[str], dict[str, str]]:
    doc = json.loads(text)
    found: dict[str, str] = {}

    def walk(node, path: str):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                p = f"{path}.{k}" if path else str(k)
                if is_secret_key(str(k)) and not (isinstance(v, bool) or v is None):
                    found[p] = json.dumps(v, sort_keys=True)
                    out[k] = REDACTED
                else:
                    out[k] = walk(v, p)
            return out
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str) and has_secret_value(node):
            found[path or "value"] = node
            return scrub_value(node)
        return node

    red = walk(doc, "")
    return (json.dumps(red, indent=2, ensure_ascii=False) + "\n").splitlines(keepends=True), found
