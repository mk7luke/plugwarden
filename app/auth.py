"""Authentication (Cloudflare Access JWT), Host allowlist and CSRF checks.

Modes (LGT_AUTH):
  cf-access  every request must carry a valid Cloudflare Access JWT (header Cf-Access-Jwt-Assertion or
             cookie CF_Authorization): RS256, signed by a key from https://<team>/cdn-cgi/access/certs,
             aud == LGT_CF_AUD, iss == https://<team>, exp/nbf valid. The user is the token's email.
  none       no authentication; only allowed when bound to loopback (dev/serve.sh), and non-loopback
             clients are refused anyway. The user is "local".
The plain Cf-Access-Authenticated-User-Email header is never trusted.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from typing import Any

import httpx
import jwt

from . import config

JWT_HEADER = "cf-access-jwt-assertion"
JWT_COOKIE = "CF_Authorization"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "lgt-amp-sync"
JWKS_TTL = 3600
JWKS_MIN_REFRESH = 30  # at most one refetch per 30 s when an unknown kid shows up

# Tests replace this with httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_startup() -> None:
    """Refuse to run unauthenticated on a non-loopback bind, or cf-access without its settings."""
    if config.AUTH_MODE not in ("cf-access", "none"):
        raise RuntimeError(f"LGT_AUTH must be 'cf-access' or 'none', not {config.AUTH_MODE!r}")
    if config.AUTH_MODE == "none" and not is_loopback(config.BIND):
        raise RuntimeError(f"LGT_AUTH=none is only allowed when bound to loopback (LGT_BIND={config.BIND!r}); "
                           "set LGT_CF_TEAM_DOMAIN and LGT_CF_AUD to use Cloudflare Access")
    if config.AUTH_MODE == "none" and config.HOSTNAME:
        raise RuntimeError(f"LGT_HOSTNAME={config.HOSTNAME!r} marks a public deployment, which must not run "
                           "without authentication; set LGT_CF_TEAM_DOMAIN and LGT_CF_AUD")
    if config.AUTH_MODE == "cf-access" and not (config.CF_TEAM_DOMAIN and config.CF_AUD):
        raise RuntimeError("LGT_AUTH=cf-access needs LGT_CF_TEAM_DOMAIN and LGT_CF_AUD")


# ---------------------------------------------------------------- JWKS cache

class _Jwks:
    def __init__(self):
        self.keys: dict[str, Any] = {}
        self.fetched = 0.0
        self.lock = threading.Lock()

    def _fetch(self) -> None:
        url = f"https://{config.CF_TEAM_DOMAIN}/cdn-cgi/access/certs"
        try:
            with httpx.Client(timeout=5.0, transport=TRANSPORT) as c:
                r = c.get(url)
                r.raise_for_status()
                data = r.json()
            keys = {}
            for k in data.get("keys", []):
                if isinstance(k, dict) and k.get("kid") and k.get("kty") == "RSA":
                    try:
                        keys[k["kid"]] = jwt.PyJWK(k, algorithm="RS256").key
                    except jwt.PyJWKError:
                        continue
        except (httpx.HTTPError, ValueError):
            if not self.keys:
                raise
            # Keep serving the cached keys; retry in a minute instead of on every request.
            self.fetched = time.time() - JWKS_TTL + 60
            return
        self.keys, self.fetched = keys, time.time()

    def get(self, kid: str):
        with self.lock:
            now = time.time()
            if not self.keys or now - self.fetched > JWKS_TTL:
                self._fetch()
            elif kid not in self.keys and now - self.fetched > JWKS_MIN_REFRESH:
                self._fetch()  # key rotation
                if kid not in self.keys:
                    self.fetched = max(self.fetched, now)  # don't let unknown kids hammer the endpoint
            return self.keys.get(kid)

    def reset(self) -> None:
        with self.lock:
            self.keys, self.fetched = {}, 0.0


JWKS = _Jwks()


def verify_token(token: str) -> str:
    """Validate a Cloudflare Access JWT and return the user identity (email, or service token name)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AuthError(401, "malformed access token")
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise AuthError(401, "access token must be RS256 with a key id")
    try:
        key = JWKS.get(header["kid"])
    except (httpx.HTTPError, ValueError) as e:
        raise AuthError(503, f"cannot fetch Cloudflare Access keys: {e}")
    if key is None:
        raise AuthError(401, "access token signed by an unknown key")
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=config.CF_AUD,
                            issuer=f"https://{config.CF_TEAM_DOMAIN}", leeway=30,
                            options={"require": ["exp", "iat", "aud", "iss"]})
    except jwt.PyJWTError as e:
        raise AuthError(401, f"invalid access token: {e}")
    who = claims.get("email") or claims.get("common_name")
    if not who or not isinstance(who, str):
        raise AuthError(401, "access token has no identity")
    return who[:200]


# ---------------------------------------------------------------- per-request checks

def host_ok(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.split(":")[0]
    return host in config.ALLOWED_HOSTS


def authenticate(headers, cookies, client_host: str | None) -> str:
    if config.AUTH_MODE == "none":
        if not is_loopback(client_host):
            raise AuthError(403, "unauthenticated mode only serves loopback clients")
        if any(headers.get(h) for h in ("cf-connecting-ip", JWT_HEADER, "x-forwarded-for")):
            raise AuthError(403, "unauthenticated mode refuses proxied requests; configure Cloudflare Access")
        return "local"
    token = headers.get(JWT_HEADER) or cookies.get(JWT_COOKIE)
    if not token:
        raise AuthError(401, "Cloudflare Access token required")
    return verify_token(token)


def csrf_ok(method: str, path: str, headers) -> str | None:
    """Returns an error message for a state-changing API request that may be cross-site, else None."""
    if method in ("GET", "HEAD", "OPTIONS") or not path.startswith("/api/"):
        return None
    if headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return "cross-site request refused"
    origin = headers.get("origin")
    if origin is not None:
        if origin == "null" or not host_ok(origin.split("://", 1)[-1]):
            return "request origin is not an allowed host"
    if headers.get(CSRF_HEADER) != CSRF_VALUE:
        return f"missing {CSRF_HEADER}: {CSRF_VALUE} header"
    return None
