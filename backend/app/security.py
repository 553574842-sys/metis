import hashlib
import hmac
import secrets
from ipaddress import ip_address
from urllib.parse import urlsplit
from datetime import timedelta
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .config import settings
from .db import get_db, now, utc
from .models import Key, Principal


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + value.hex()


def password_matches(password, encoded):
    salt, expected = encoded.split(':')
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(value.hex(), expected)


def initialize_admin(db):
    if not db.scalar(select(Principal).where(Principal.name == 'admin')):
        if len(settings.admin_password) < 12 or settings.admin_password.startswith('CHANGE_ME'):
            if settings.local_passwordless:
                return
            raise RuntimeError('设置至少 12 位 ADMIN_PASSWORD；可运行 scripts/init.ps1 自动生成。')
        db.add(Principal(name='admin', password_hash=password_hash(settings.admin_password)))
        db.commit()


def direct_local_access(request: Request):
    if not settings.local_passwordless or not request.client or request.headers.get('authorization'):
        return False
    try:
        if not ip_address(request.client.host).is_loopback:
            return False
        host = request.headers.get('host', '')
        authority = urlsplit('//' + host)
        if authority.hostname not in {'localhost', '127.0.0.1', '::1'} or authority.path or authority.query or authority.fragment:
            return False
        authority.port  # 拒绝非法端口格式。
    except ValueError:
        return False
    # 代理和外网手机继续使用原有认证，不能借代理的回环连接免密。
    if any(name.startswith('x-forwarded-') or name.startswith('tailscale-') or name in {'forwarded', 'x-real-ip'}
           for name in request.headers):
        return False
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{host}':
        return False
    if request.headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'}:
        return False
    return True


def authenticate(request: Request, db=Depends(get_db)):
    header = request.headers.get('authorization', '')
    is_bearer = header.startswith('Bearer ')
    token = header[7:] if is_bearer else request.cookies.get('atlas_session', '')
    key = db.scalar(select(Key).where(Key.key_hash == digest(token), Key.revoked_at.is_(None))) if token else None
    if not key or (key.expires_at and utc(key.expires_at) <= now()):
        raise HTTPException(401, '请登录或提供有效 API Key')
    if is_bearer and key.kind != 'agent':
        raise HTTPException(401, '会话凭据只能通过 Cookie 使用')
    if not is_bearer and key.kind not in {'session', 'local_session'}:
        raise HTTPException(401, '无效会话')
    if key.kind == 'local_session' and not direct_local_access(request):
        raise HTTPException(401, '本机会话仅限直接在本机使用')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        if key.scope != 'admin':
            # Agent 只读检索允许 POST 问答与比较，无写入副作用。
            if request.url.path not in {'/api/v1/knowledge/ask', '/api/v1/knowledge/compare'}:
                raise HTTPException(403, '此 API Key 仅允许只读检索')
        if not is_bearer:
            origin = request.headers.get('origin')
            allowed = settings.allowed_origins.split(',')
            if origin and origin not in allowed:
                raise HTTPException(403, '请求来源未获授权')
            csrf = request.headers.get('x-csrf-token', '')
            if not csrf or not hmac.compare_digest(key.csrf_hash or '', digest(csrf)):
                raise HTTPException(403, 'CSRF 校验失败')
    request.state.actor = key.name
    return key


def admin(key=Depends(authenticate)):
    if key.scope != 'admin':
        raise HTTPException(403, '需要管理员权限')
    return key


def create_session(db, local=False):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(Key(name='local' if local else 'admin', key_hash=digest(token), csrf_hash=digest(csrf),
               kind='local_session' if local else 'session', scope='admin', expires_at=now() + timedelta(hours=12)))
    db.commit()
    return token, csrf
