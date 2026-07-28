import base64
import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ngx_research.models import AuthToken, User

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
TOKEN_TTL_DAYS = 30
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    pass


def create_user(
    session: Session,
    email: str,
    password: str,
    full_name: str | None = None,
) -> tuple[User, str, datetime]:
    normalized_email = _normalize_email(email)
    _validate_password(password)
    existing = session.scalar(select(User).where(User.email == normalized_email))
    if existing:
        raise AuthError("email is already registered")

    user = User(
        email=normalized_email,
        full_name=full_name.strip() if full_name else None,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    token, expires_at = issue_token(session, user)
    session.commit()
    session.refresh(user)
    return user, token, expires_at


def login_user(session: Session, email: str, password: str) -> tuple[User, str, datetime]:
    user = session.scalar(select(User).where(User.email == _normalize_email(email)))
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")
    if not user.is_active:
        raise AuthError("account is disabled")

    token, expires_at = issue_token(session, user)
    session.commit()
    session.refresh(user)
    return user, token, expires_at


def issue_token(session: Session, user: User) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=TOKEN_TTL_DAYS)
    session.add(
        AuthToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
    )
    return raw_token, expires_at


def user_from_bearer_token(session: Session, authorization: str | None) -> User:
    token = _bearer_token(authorization)
    token_record = session.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    now = datetime.now(UTC).replace(tzinfo=None)
    if not token_record or token_record.revoked_at or token_record.expires_at <= now:
        raise AuthError("invalid or expired token")

    user = session.get(User, token_record.user_id)
    if not user or not user.is_active:
        raise AuthError("account is disabled")
    return user


def revoke_bearer_token(session: Session, authorization: str | None) -> None:
    token = _bearer_token(authorization)
    token_record = session.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if token_record and not token_record.revoked_at:
        token_record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        session.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), expected)
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.match(normalized):
        raise AuthError("enter a valid email address")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("authorization header must use bearer token")
    return token.strip()
