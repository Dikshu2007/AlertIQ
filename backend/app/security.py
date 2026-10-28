"""Security primitives: password hashing, tokens, Fernet-encrypted credentials.

API keys for integrations and LLM providers are stored encrypted (Fernet) and
are never returned to the frontend or written to logs.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

# ---------------------------------------------------------------- passwords
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- tokens (HS256 JWT)
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(user_id: int) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"sub": str(user_id), "exp": int(time.time()) + settings.access_token_expire_minutes * 60}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = _b64url(hmac.new(settings.secret_key.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def decode_access_token(token: str) -> int:
    try:
        header, payload, sig = token.split(".")
        expected = _b64url(hmac.new(settings.secret_key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        data = json.loads(_b64url_decode(payload))
        if int(data["exp"]) < time.time():
            raise ValueError("expired")
        return int(data["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


# ---------------------------------------------------------------- credential encryption
def _fernet() -> Fernet:
    if settings.encryption_key:
        return Fernet(settings.encryption_key.encode())
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt credential: ENCRYPTION_KEY/SECRET_KEY changed") from exc


# ---------------------------------------------------------------- FastAPI auth
_bearer = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return decode_access_token(credentials.credentials)


# ---------------------------------------------------------------- naive per-IP rate limiter
import collections  # noqa: E402

_BUCKETS: dict[str, collections.deque] = {}


def rate_limit(ip: str, limit: int | None = None) -> None:
    limit = limit or settings.rate_limit_per_minute
    now = time.time()
    key = f"{ip}:{limit}"  # buckets are per limit-class so strict auth limits never starve webhook/auth flows
    bucket = _BUCKETS.setdefault(key, collections.deque())
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    bucket.append(now)