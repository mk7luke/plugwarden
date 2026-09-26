"""Server-specific config keys: detection and format-preserving merges.

Pushing a shared config (LuckPerms/config.yml) must not overwrite values that are meant to differ per
server (`server: hub`). This module parses config files line by line into leaf keys, flags keys that look
server-specific, and can write "the source file, but with the target's values for these keys" by
substituting scalar values on the source's own lines (comments and formatting survive). Anything it
cannot address unambiguously is reported as unsafe instead of guessed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_SUFFIXES = (".yml", ".yaml", ".properties", ".toml", ".json", ".conf")
MAX_BYTES = 256 * 1024

# Last key segment names that usually identify the server rather than the network.
IDENTITY_NAME = re.compile(
    r"^(server|servers?[-_]?name|server[-_]?id|serverid|server[-_]?display[-_]?name|context|contexts|world|"
    r"world[-_]?name|level[-_]?name|name|id|port|server[-_]?port|query[-_]?port|motd|title|display|"
    r"display[-_]?name|channel|channel[-_]?id|[a-z]*channel[-_]?id|database|table[-_]?prefix|host|ip|"
    r"address|bungee[-_]?server[-_]?name|proxy[-_]?server[-_]?name|lobby|hub)$", re.I)
SECRET_NAME = re.compile(r"(password|passwd|secret|token|api[-_]?key|webhook|jdbc|private[-_]?key|license)", re.I)
REDACTED = "«redacted»"


@dataclass
class Leaf:
    line: int        # index into the file's lines
    value: str       # raw value text as written (quotes kept, comment removed)
    prefix: str      # everything before the value on that line
    suffix: str      # trailing comment/whitespace/comma after the value (without newline)


@dataclass
class Parsed:
    leaves: dict[str, Leaf]
    unsafe: set[str]  # key paths that exist but cannot be addressed as one scalar line
    aliases: bool = False  # YAML anchors/aliases: one edit can change several places


def is_config(rel: str) -> bool:
    return rel.lower().endswith(CONFIG_SUFFIXES)


def _split_comment(val: str) -> tuple[str, str]:
    """Split 'value  # comment'. A quote only opens a quoted scalar at the start of the value
    (so `bob's # note` keeps its comment). Returns (value, rest)."""
    q = val[0] if val[:1] in ("'", '"') else None
    start = 1 if q else 0
    i = start
    while i < len(val):
        ch = val[i]
        i += 1
        if q:
            if q == '"' and ch == "\\":
                i += 1  # escaped character inside a double-quoted scalar
            elif ch == q:
                q = None
        elif ch == "#" and (i == 1 or val[i - 2] in " \t"):
            j = i - 1
            while j > 0 and val[j - 1] in " \t":
                j -= 1
            return val[:j], val[j:]
    stripped = val.rstrip()
    return stripped, val[len(stripped):]


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def same_value(a: str, b: str) -> bool:
    return _unquote(a) == _unquote(b)


_YAML_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>\"[^\"]*\"|'[^']*'|[^\s#'\"\-][^:#]*?|-[^\s:#][^:#]*?)\s*:(?=\s|$)(?P<rest>.*)$")


def parse_yaml(lines: list[str]) -> Parsed:
    leaves: dict[str, Leaf] = {}
    unsafe: set[str] = set()
    stack: list[tuple[int, str]] = []
    block_indent: int | None = None
    list_indent: int | None = None
    last: tuple[str, int] | None = None  # most recent scalar leaf (path, indent)
    aliases = False
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        if list_indent is not None:
            if indent > list_indent or (indent == list_indent and line.lstrip().startswith("- ")):
                continue
            list_indent = None
        if line.lstrip().startswith("- ") or line.strip() == "-":
            # Sequence under the current key: its items are not addressable by key path.
            if stack:
                unsafe.add(".".join(k for _, k in stack))
            list_indent = indent
            continue
        m = _YAML_KEY.match(line)
        if not m:
            # A continuation line of a multi-line plain/quoted scalar: the previous leaf spans lines.
            if last is not None and indent > last[1]:
                unsafe.add(last[0])
            continue
        key = _unquote(m.group("key"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        rest = m.group("rest")
        value, suffix = _split_comment(rest.lstrip())
        if key == "<<" or value[:1] in ("&", "*"):
            aliases = True
            value = "" if value[:1] == "&" and " " not in value else value
            if not value:
                stack.append((indent, key))
                last = None
                continue
        if not value:
            stack.append((indent, key))
            last = (path, indent)  # a following deeper non-key line would be this key's plain value
            continue
        if value[:1] in ("|", ">") or (value[:1] in "[{" and not _balanced(value)) or \
                (value[:1] in "'\"" and not (len(value) > 1 and value[-1] == value[0])):
            unsafe.add(path)  # multi-line value
            block_indent = indent
            continue
        if path in leaves:
            unsafe.add(path)
            continue
        start = len(line) - len(rest.lstrip()) if rest.strip() else len(line)
        leaves[path] = Leaf(i, value, line[:start], suffix)
        last = (path, indent)
    for p in unsafe:
        leaves.pop(p, None)
    return Parsed(leaves, unsafe, aliases)


def _balanced(v: str) -> bool:
    return v.count("[") == v.count("]") and v.count("{") == v.count("}")


_PROP = re.compile(r"^(?P<pre>\s*(?P<key>[^#!\s=:][^=:]*?)\s*[=:]\s*)(?P<val>.*)$")
_TOML_SECTION = re.compile(r"^\s*\[(?P<arr>\[)?\s*(?P<name>[^\]]+?)\s*\]\]?\s*(#.*)?$")
_FLAT = re.compile(r"^(?P<pre>\s*\"?(?P<key>[A-Za-z0-9_.\-]+)\"?\s*[:=]\s*)(?P<val>.*?)(?P<post>\s*,?\s*)$")


def parse_properties(lines: list[str]) -> Parsed:
    leaves, unsafe = {}, set()
    continuing = False
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if continuing:  # continuation of the previous logical line: never a key of its own
            continuing = line.endswith("\\")
            continue
        if not line.strip() or line.lstrip()[:1] in "#!":
            continue
        continuing = line.endswith("\\")
        m = _PROP.match(line)
        if not m:
            continue
        key, val = m.group("key").strip(), m.group("val")
        if val.endswith("\\") or key in leaves:
            unsafe.add(key)
            continue
        stripped = val.rstrip()
        leaves[key] = Leaf(i, stripped, m.group("pre"), val[len(stripped):])
    for p in unsafe:
        leaves.pop(p, None)
    return Parsed(leaves, unsafe)


def parse_toml(lines: list[str]) -> Parsed:
    leaves, unsafe = {}, set()
    section, in_array = "", False
    in_string: str | None = None
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if in_string:  # inside a multi-line string: nothing here is a key
            if in_string in line:
                in_string = None
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        s = _TOML_SECTION.match(line)
        if s:
            section, in_array = s.group("name").strip(), bool(s.group("arr"))
            if in_array:
                unsafe.add(section)
            continue
        m = _PROP.match(line)
        if not m or "=" not in line.split("#")[0]:
            continue
        key = _unquote(m.group("key").strip())
        path = f"{section}.{key}" if section else key
        value, suffix = _split_comment(m.group("val"))
        for delim in ('"""', "'''"):
            if value.startswith(delim) and (len(value) < 6 or not value.endswith(delim)):
                in_string = delim
        if in_string or value.startswith(('"""', "'''")) or in_array or path in leaves or \
                (value[:1] in "[{" and not _balanced(value)):
            unsafe.add(path)
            continue
        leaves[path] = Leaf(i, value, m.group("pre"), suffix)
    for p in unsafe:
        leaves.pop(p, None)
    return Parsed(leaves, unsafe)


_JSON_SCALAR = re.compile(r'^(?:"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)$')


def parse_flat(lines: list[str]) -> Parsed:
    """JSON/HOCON: addressed by leaf key name only; a name that appears twice is unsafe, and only a
    lone JSON scalar on its own line counts as a value (never `"a": 1, "b": 2` or `"a": 1}`)."""
    leaves, unsafe = {}, set()
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        m = _FLAT.match(line)
        if not m:
            continue
        key, val = m.group("key"), m.group("val").strip()
        if key in leaves or key in unsafe:
            unsafe.add(key)
            continue
        if not _JSON_SCALAR.match(val):
            unsafe.add(key)  # object/array/anything else on the line: not a single scalar
            continue
        leaves[key] = Leaf(i, m.group("val"), m.group("pre"), m.group("post"))
    for p in unsafe:
        leaves.pop(p, None)
    return Parsed(leaves, unsafe)


def parse(rel: str, lines: list[str]) -> Parsed:
    low = rel.lower()
    if low.endswith((".yml", ".yaml")):
        return parse_yaml(lines)
    if low.endswith(".properties"):
        return parse_properties(lines)
    if low.endswith(".toml"):
        return parse_toml(lines)
    return parse_flat(lines)


def read_lines(path: Path) -> list[str] | None:
    """Text lines of a small config file, or None if missing/too large/binary."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
        if b"\x00" in data:
            return None
        return data.decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def _shown(key: str, value: str | None) -> str | None:
    from .redact import shown
    return shown(key, value)


def server_specific(rel: str, src: Parsed, tgt: Parsed, others: list[Parsed]) -> list[dict]:
    """Keys whose value differs source→target and look server-specific: the name looks like identity,
    or the non-source servers that have this file disagree among themselves (each has its own value)."""
    out = []
    for key in sorted(tgt.unsafe - src.leaves.keys()):
        if IDENTITY_NAME.match(key.split(".")[-1]):
            # A list/multi-line value under an identity-like name: can't compare or keep it line-wise.
            out.append({"key": key, "source_value": None, "target_value": None, "reason": "complex"})
    for key, tl in tgt.leaves.items():
        sl = src.leaves.get(key)
        if sl is None:
            if IDENTITY_NAME.match(key.split(".")[-1]) and key not in src.unsafe:
                out.append({"key": key, "source_value": None, "target_value": _shown(key, tl.value),
                            "reason": "target_only"})  # the push would drop this server's value
            continue
        if same_value(sl.value, tl.value):
            continue
        name = key.split(".")[-1]
        values = {_unquote(p.leaves[key].value) for p in [tgt] + others if key in p.leaves}
        reason = "name" if IDENTITY_NAME.match(name) else ("varies" if len(values) >= 2 else None)
        if reason:
            out.append({"key": key, "source_value": _shown(key, sl.value), "target_value": _shown(key, tl.value),
                        "reason": reason})
    return out


class MergeUnsafe(Exception):
    pass


def merge(rel: str, src_lines: list[str], tgt_lines: list[str], keep: list[str]) -> tuple[list[str], list[str]]:
    """Source file with the target's values for `keep`. Returns (lines, keys actually kept).

    Raises MergeUnsafe when a key cannot be substituted as a single scalar line on both sides, or when
    the result does not re-parse to exactly source + overrides."""
    low = rel.lower()
    if low.endswith(".conf"):
        raise MergeUnsafe("HOCON (.conf) files are not merged")
    if "\\" in rel:
        raise MergeUnsafe("file name contains a backslash")
    src, tgt = parse(rel, src_lines), parse(rel, tgt_lines)
    if src.aliases or tgt.aliases:
        raise MergeUnsafe("file uses YAML anchors/aliases")
    real = _real_docs(rel, src_lines, tgt_lines)
    out = list(src_lines)
    kept = []
    for key in keep:
        in_src, in_tgt = key in src.leaves, key in tgt.leaves
        if _touches_unsafe(key, src.unsafe) or _touches_unsafe(key, tgt.unsafe):
            raise MergeUnsafe(f"'{key}' is not a single-line value")
        if not in_tgt:
            if real is not None and _lookup(real[1], key, low) is not _MISSING:
                raise MergeUnsafe(f"'{key}' exists on the target in a form that can't be kept line-wise")
            if real is None and _mentioned(tgt_lines, key):
                raise MergeUnsafe(f"'{key}' appears on the target but could not be located safely")
            continue  # target provably never had it: nothing to preserve
        if not in_src:
            raise MergeUnsafe(f"'{key}' exists on the target but not in the source file")
        sl, tl = src.leaves[key], tgt.leaves[key]
        if same_value(sl.value, tl.value):
            continue
        nl = "\n" if out[sl.line].endswith("\n") else ""
        if out[sl.line].endswith("\r\n"):
            nl = "\r\n"
        out[sl.line] = f"{sl.prefix}{tl.value}{sl.suffix}{nl}"
        kept.append(key)
    # Verify 1: line view — every leaf equals the source's, except the kept ones which equal the target's.
    check = parse(rel, out)
    expected = {k: (tgt.leaves[k].value if k in kept else v.value) for k, v in src.leaves.items()}
    got = {k: v.value for k, v in check.leaves.items()}
    if got != expected or check.unsafe != src.unsafe:
        raise MergeUnsafe("merged file did not re-parse to the expected values")
    # Verify 2: a real parser agrees the result is exactly source + the target's values for kept keys.
    if real is not None:
        want = copy.deepcopy(real[0])
        for key in kept:
            tv = _lookup(real[1], key, low)
            # A single-line flow value ({"global": "…"}) is replaced as a whole; the comparison below
            # checks the entire parsed result, so a subtree value is still verified exactly.
            if tv is _MISSING or not _assign(want, key, tv, low):
                raise MergeUnsafe(f"'{key}' does not map to one scalar in the parsed file")
        try:
            merged = _load(low, "".join(out))
        except ValueError as e:
            raise MergeUnsafe(f"merged file does not parse: {e}")
        if merged != want:
            raise MergeUnsafe("merged file differs from source + kept values")
    elif low.endswith(".json"):
        raise MergeUnsafe("JSON file does not parse")
    elif low.endswith((".yml", ".yaml")):
        _plain_yaml_only(src_lines + tgt_lines, keep)
    return out, kept


_MISSING = object()


def _load(low: str, text: str):
    import json

    import yaml
    try:
        if low.endswith(".json"):
            return json.loads(text)
        if low.endswith((".yml", ".yaml")):
            docs = list(yaml.safe_load_all(text))
            if len(docs) > 1:
                raise ValueError("multiple YAML documents")
            return docs[0] if docs else None
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(str(e))
    raise ValueError("no parser")


def _real_docs(rel: str, src_lines: list[str], tgt_lines: list[str]):
    """(source, target) parsed by a real parser, or None when this format/dialect has none."""
    low = rel.lower()
    if not low.endswith((".json", ".yml", ".yaml")):
        return None
    try:
        return _load(low, "".join(src_lines)), _load(low, "".join(tgt_lines))
    except ValueError as e:
        if "multiple YAML documents" in str(e):
            raise MergeUnsafe("multiple YAML documents")
        if low.endswith(".json"):
            raise MergeUnsafe(f"JSON does not parse: {e}")
        return None  # plugin YAML dialect (e.g. Plan's %placeholders%): fall back to strict line checks


def _json_paths(doc, name: str, prefix=()) -> list[tuple]:
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == name:
                out.append(prefix + (k,))
            out += _json_paths(v, name, prefix + (k,))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out += _json_paths(v, name, prefix + (i,))
    return out


def _lookup(doc, key: str, low: str):
    if low.endswith(".json"):
        paths = _json_paths(doc, key)
        if len(paths) != 1:
            return _MISSING
        path = paths[0]
    else:
        path = _yaml_path(doc, key)
        if path is None:
            return _MISSING
    cur = doc
    for p in path:
        cur = cur[p]
    return cur


def _yaml_path(doc, key: str):
    """Resolve a dotted key path against a parsed YAML mapping (keys may themselves contain dots)."""
    parts = key.split(".")

    def walk(node, i, acc):
        if i == len(parts):
            return acc
        if not isinstance(node, dict):
            return None
        for j in range(len(parts), i, -1):
            k = ".".join(parts[i:j])
            for cand in (k,) + ((int(k),) if k.lstrip("-").isdigit() else ()) + \
                    ((True,) if k in ("true", "yes", "on") else ()) + ((False,) if k in ("false", "no", "off") else ()):
                if cand in node:
                    r = walk(node[cand], j, acc + (cand,))
                    if r is not None:
                        return r
        return None
    return walk(doc, 0, ())


def _assign(doc, key: str, value, low: str) -> bool:
    path = _json_paths(doc, key) if low.endswith(".json") else [_yaml_path(doc, key)]
    if len(path) != 1 or path[0] is None:
        return False
    cur = doc
    for p in path[0][:-1]:
        cur = cur[p]
    cur[path[0][-1]] = value
    return True


def _touches_unsafe(key: str, unsafe: set[str]) -> bool:
    return any(key == u or key.startswith(u + ".") or u.startswith(key + ".") for u in unsafe)


def _mentioned(lines: list[str], key: str) -> bool:
    seg = re.escape(key.split(".")[-1])
    pat = re.compile(r"(^|[\s{,\"'])" + seg + r"[\"']?\s*[:=]")
    return any(pat.search(ln) for ln in lines)


def _plain_yaml_only(lines: list[str], keep: list[str]) -> None:
    """For YAML dialects PyYAML rejects, only allow the simplest shapes."""
    for ln in lines:
        if ln.startswith("---") or ln.startswith("...") or "\t" in ln[:len(ln) - len(ln.lstrip())]:
            raise MergeUnsafe("unparseable YAML with document markers or tab indentation")
    for key in keep:
        if not all(re.fullmatch(r"[A-Za-z0-9_\- ]+", seg) for seg in key.split(".")):
            raise MergeUnsafe(f"'{key}' has characters that make line-based merging ambiguous")
