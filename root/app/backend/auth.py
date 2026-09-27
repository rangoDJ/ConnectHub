import os
import json
import time
import base64
import hashlib
import secrets
import logging
import ipaddress
import threading
from typing import Optional, Dict, Any, List
from pathlib import Path
from fastapi import Request, HTTPException, status, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import httpx

logger = logging.getLogger("auth")

def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

# Authentication configuration from environment
AUTH_MODE = os.environ.get("AUTH_MODE", "none").lower() # none, basic, oidc, forward_auth
# Basic auth: set true to let visitors create more accounts from the login page
ALLOW_SIGNUPS = _env_bool("ALLOW_SIGNUPS", False)

# OIDC / Authentik configuration
OIDC_ISSUER_URL = os.environ.get("OIDC_ISSUER_URL", "").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
OIDC_SCOPES = os.environ.get("OIDC_SCOPES", "openid email profile")
# TLS verification for the OIDC provider; only disable for self-signed test setups
OIDC_VERIFY_SSL = _env_bool("OIDC_VERIFY_SSL", True)

# Forward Auth header (Authentik Proxy, Authelia, Traefik, Cloudflare Access)
FORWARD_AUTH_HEADER = os.environ.get("FORWARD_AUTH_HEADER", "X-authentik-username")
FORWARD_AUTH_EMAIL_HEADER = os.environ.get("FORWARD_AUTH_EMAIL_HEADER", "X-authentik-email")
# Comma-separated IPs/CIDRs of the reverse proxy allowed to set the forward auth header
FORWARD_AUTH_TRUSTED_PROXIES = os.environ.get("FORWARD_AUTH_TRUSTED_PROXIES", "")

# Session cookie "Secure" flag: auto (follow request scheme), true, false
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").lower()

# Secret key persistence for session cookies
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
SECRET_KEY_FILE = CONFIG_DIR / ".session_secret"

def get_or_create_secret_key() -> str:
    env_secret = os.environ.get("SECRET_KEY")
    if env_secret:
        return env_secret
    try:
        if SECRET_KEY_FILE.exists():
            # A truncated file (crash or full disk mid-write) must not become an empty
            # signing key, which would let anyone forge a session cookie
            existing = SECRET_KEY_FILE.read_text().strip()
            if existing:
                return existing
            logger.warning("Session secret file is empty; generating a new key")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_hex(32)
        # Write via a private temp file so the real path is never briefly empty
        tmp = SECRET_KEY_FILE.with_name(SECRET_KEY_FILE.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new_secret)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SECRET_KEY_FILE)
        return new_secret
    except Exception as e:
        logger.warning("Could not persist session secret (%s); sessions will reset on restart", e)
        return secrets.token_hex(32)

SECRET_KEY = get_or_create_secret_key()
serializer = URLSafeTimedSerializer(SECRET_KEY)
state_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="oidc-state")
COOKIE_NAME = "connecthub_session"
STATE_COOKIE_NAME = "connecthub_oidc_state"
STATE_MAX_AGE = 600
MAX_AGE = 86400 * 7 # 7 days session lifetime

def _parse_networks(raw: str) -> List[ipaddress._BaseNetwork]:
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.error("Ignoring invalid FORWARD_AUTH_TRUSTED_PROXIES entry: %s", part)
    return nets

TRUSTED_PROXY_NETS = _parse_networks(FORWARD_AUTH_TRUSTED_PROXIES)

# Startup configuration sanity checks
if AUTH_MODE == "forward_auth" and not TRUSTED_PROXY_NETS:
    logger.error("AUTH_MODE=forward_auth but FORWARD_AUTH_TRUSTED_PROXIES is empty; all requests will be rejected")
if AUTH_MODE == "oidc" and not OIDC_VERIFY_SSL:
    logger.warning("OIDC_VERIFY_SSL is disabled; OIDC traffic is vulnerable to interception")

def client_ip(request: Request) -> str:
    """Peer address as seen by nginx (X-Real-IP is always overwritten by our nginx config)."""
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else "")

def _is_trusted_proxy(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXY_NETS)

# ----------------- User accounts -----------------

# Basic auth accounts, created from the WebUI. There is no default account: on a fresh
# install the first visitor to the login page creates one. To recover a forgotten
# password, delete that user's entry (or the whole file) and restart.
USERS_FILE = CONFIG_DIR / "users.json"
USERNAME_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 1024
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_users_lock = threading.Lock()

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"

def verify_password_hash(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)

# Checked when the username doesn't exist, so an unknown user costs the same time as a
# wrong password and response timing doesn't reveal which usernames exist
_DUMMY_HASH = hash_password(secrets.token_hex(16))

def _load_users() -> Optional[Dict[str, Dict[str, str]]]:
    """{} when no account exists yet. None when the file is damaged: logins are then
    rejected and sign-up stays closed, so nobody can claim it as a fresh install."""
    try:
        users = json.loads(USERS_FILE.read_text()).get("users")
        if isinstance(users, dict) and all(
            isinstance(u, dict) and isinstance(u.get("hash"), str) and isinstance(u.get("epoch"), str)
            for u in users.values()
        ):
            return users
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError):
        pass
    logger.error("%s is unreadable; all logins are rejected until it is fixed or deleted", USERS_FILE)
    return None

def _save_users(users: Dict[str, Dict[str, str]]):
    """Caller must hold _users_lock."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Same pattern as the session secret: private temp file, then atomic replace
    tmp = USERS_FILE.with_name(USERS_FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"users": users}, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, USERS_FILE)

_users = _load_users()

if AUTH_MODE == "basic" and _users == {}:
    logger.warning("No accounts exist yet; the first visitor to the login page will create one")

def setup_required() -> bool:
    """True on a fresh install, before the first account exists."""
    return _users == {}

def signups_open() -> bool:
    # The first account can always be created, even with ALLOW_SIGNUPS=false,
    # otherwise nobody could ever log in
    return _users is not None and (not _users or ALLOW_SIGNUPS)

def user_epoch(username: str) -> Optional[str]:
    """Changes whenever the user's password does. Session cookies carry it, so a
    password change ends that user's older sessions. None for an unknown user."""
    user = (_users or {}).get(username)
    return user["epoch"] if user else None

def _new_record(password: str) -> Dict[str, str]:
    return {"hash": hash_password(password), "epoch": secrets.token_hex(8)}

def create_user(username: str, password: str):
    """Raises PermissionError when sign-up is closed, ValueError when the name is taken."""
    global _users
    record = _new_record(password)
    with _users_lock:
        # Re-checked under the lock so two visitors can't both claim a fresh install
        if not signups_open():
            raise PermissionError("Sign-ups are disabled")
        if any(name.casefold() == username.casefold() for name in _users):
            raise ValueError("That username is already taken")
        users = {**_users, username: record}
        _save_users(users)
        _users = users

def set_user_password(username: str, new_password: str):
    global _users
    record = _new_record(new_password)
    with _users_lock:
        if not _users or username not in _users:
            raise KeyError(username)
        users = {**_users, username: record}
        _save_users(users)
        _users = users

def check_basic_credentials(username: str, password: str) -> bool:
    user = (_users or {}).get(username)
    ok = verify_password_hash(password, user["hash"] if user else _DUMMY_HASH)
    return user is not None and ok

# ----------------- Login rate limiting -----------------

LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60
# How often to drop entries for IPs that stopped failing. Without this, only the IP
# being looked up is ever pruned, so a spray from many sources grows the dict forever.
LOGIN_SWEEP_SECONDS = 60
_failed_logins: Dict[str, List[float]] = {}
_failed_lock = threading.Lock()
_last_sweep = 0.0

def _sweep_expired(now: float):
    """Drop every IP whose failures have all aged out. Caller must hold _failed_lock."""
    global _last_sweep
    # A backwards clock jump (NTP) makes the delta negative and forces a sweep,
    # rather than blocking sweeps until the wall clock catches up again
    if 0 <= now - _last_sweep < LOGIN_SWEEP_SECONDS:
        return
    _last_sweep = now
    # Attempts are appended in order, so the last one is the most recent
    stale = [ip for ip, attempts in _failed_logins.items()
             if not attempts or now - attempts[-1] >= LOGIN_WINDOW_SECONDS]
    for ip in stale:
        del _failed_logins[ip]

def _recent_failures(ip: str, now: float) -> List[float]:
    attempts = [t for t in _failed_logins.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    if attempts:
        _failed_logins[ip] = attempts
    else:
        _failed_logins.pop(ip, None)
    return attempts

def check_login_rate_limit(ip: str):
    with _failed_lock:
        if len(_recent_failures(ip, time.time())) >= LOGIN_MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later."
            )

def record_login_failure(ip: str):
    with _failed_lock:
        now = time.time()
        # Only this path adds keys, so it is the one that has to bound the dict
        _sweep_expired(now)
        _recent_failures(ip, now)
        _failed_logins.setdefault(ip, []).append(now)

def clear_login_failures(ip: str):
    with _failed_lock:
        _failed_logins.pop(ip, None)

# ----------------- OIDC -----------------

# Cache OIDC OpenID configuration
_oidc_config: Optional[Dict[str, Any]] = None

def oidc_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=OIDC_VERIFY_SSL, timeout=10.0)

async def get_oidc_config() -> Dict[str, Any]:
    global _oidc_config
    if _oidc_config is not None:
        return _oidc_config
    if not OIDC_ISSUER_URL:
        raise HTTPException(status_code=500, detail="OIDC_ISSUER_URL not configured")

    discovery_urls = [
        f"{OIDC_ISSUER_URL}/.well-known/openid-configuration",
        f"{OIDC_ISSUER_URL}/application/o/.well-known/openid-configuration"
    ]
    async with oidc_http_client() as client:
        for url in discovery_urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    _oidc_config = resp.json()
                    logger.info("Discovered OIDC endpoints from %s", url)
                    return _oidc_config
            except Exception as e:
                logger.warning("Failed OIDC discovery on %s: %s", url, e)
    raise HTTPException(status_code=500, detail="Could not retrieve OIDC configuration from issuer")

# ----------------- Cookies -----------------

def _cookie_secure(request: Optional[Request]) -> bool:
    if COOKIE_SECURE in ("1", "true", "yes", "on"):
        return True
    if COOKIE_SECURE in ("0", "false", "no", "off"):
        return False
    if request is None:
        return False
    proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"

def create_session_cookie(response: Response, user_data: dict, request: Optional[Request] = None):
    if user_data.get("auth_mode") == "basic":
        user_data = {**user_data, "pwv": user_epoch(user_data.get("username", ""))}
    token = serializer.dumps(user_data)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request)
    )

def clear_session_cookie(response: Response):
    response.delete_cookie(key=COOKIE_NAME)

def create_state_cookie(response: Response, state: str, request: Optional[Request] = None):
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state_serializer.dumps(state),
        max_age=STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request)
    )

def verify_state_cookie(request: Request, state: Optional[str]) -> bool:
    token = request.cookies.get(STATE_COOKIE_NAME)
    if not token or not state:
        return False
    try:
        expected = state_serializer.loads(token, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return secrets.compare_digest(str(expected), state)

def clear_state_cookie(response: Response):
    response.delete_cookie(key=STATE_COOKIE_NAME)

# ----------------- User resolution -----------------

def get_current_user(request: Request) -> Dict[str, Any]:
    """Dependency that resolves and validates the current user based on AUTH_MODE."""
    if AUTH_MODE == "none":
        return {"authenticated": True, "username": "guest", "auth_mode": "none"}

    # 1. Forward Auth header (Authentik Outpost / Traefik / Authelia), only from a trusted proxy
    if AUTH_MODE == "forward_auth":
        val = request.headers.get(FORWARD_AUTH_HEADER)
        if val:
            ip = client_ip(request)
            if not _is_trusted_proxy(ip):
                logger.warning("Rejected forward auth header from untrusted address %s", ip)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Untrusted forward authentication source")
            return {
                "authenticated": True,
                "username": val,
                "email": request.headers.get(FORWARD_AUTH_EMAIL_HEADER, ""),
                "auth_mode": "forward_auth"
            }
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forward authentication header missing")

    # 2. Session Cookie (Basic Auth / OIDC)
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        try:
            data = serializer.loads(cookie_token, max_age=MAX_AGE)
            # Basic auth sessions end when the user's password changes or the account
            # is removed from users.json
            epoch = user_epoch(data.get("username", ""))
            stale = AUTH_MODE == "basic" and (epoch is None or data.get("pwv") != epoch)
            if data.get("auth_mode") == AUTH_MODE and not stale:
                return {
                    "authenticated": True,
                    "username": data.get("username", "user"),
                    "email": data.get("email", ""),
                    "auth_mode": AUTH_MODE
                }
        except (BadSignature, SignatureExpired):
            pass

    # 3. HTTP Authorization header (Basic Auth fallback for scripts)
    auth_header = request.headers.get("Authorization")
    if AUTH_MODE == "basic" and auth_header and auth_header.startswith("Basic "):
        ip = client_ip(request)
        check_login_rate_limit(ip)
        try:
            decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
            u, p = decoded.split(":", 1)
        except Exception:
            u, p = "", ""
        if check_basic_credentials(u, p):
            clear_login_failures(ip)
            return {"authenticated": True, "username": u, "auth_mode": "basic"}
        record_login_failure(ip)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
