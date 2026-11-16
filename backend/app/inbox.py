"""Paired, limited mobile inbox; never grants a phone an administrator session."""
import hmac
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .config import ROOT
from .db import get_db, now, utc
from .models import Analysis, Audit, Creator, InboxSubmission, Job, Key, Knowledge, Work
from .pipeline import enqueue_link_capture
from .security import admin, authenticate, digest, direct_local_access

CONNECTION_FILE = ROOT / 'runtime' / 'inbox-connection.json'
COOKIE = 'atlas_inbox'
CSRF_COOKIE = 'atlas_inbox_csrf'
DEVICE_DAYS = 90
attempts = defaultdict(deque)
attempt_lock = threading.Lock()
router = APIRouter(prefix='/api/inbox')
admin_router = APIRouter(prefix='/api/v1/inbox')


def connection_url():
    try:
        if CONNECTION_FILE.is_symlink() or CONNECTION_FILE.stat().st_size > 4096:
            return ''
        value = json.loads(CONNECTION_FILE.read_text(encoding='utf-8-sig')).get('url', '')
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and re.fullmatch(r'[a-z0-9-]+\.[a-z0-9.-]+\.ts\.net', parsed.hostname or '')
                and parsed.port in {None, 443} and not parsed.username and not parsed.password
                and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment):
            return value.rstrip('/')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ''


def origin_for(request):
    if direct_local_access(request):
        return str(request.base_url).rstrip('/')
    base = connection_url()
    try:
        host = urlsplit('//' + request.headers.get('host', ''))
        if base and host.hostname == urlsplit(base).hostname and host.port in {None, 443}:
            return base
    except ValueError:
        pass
    raise HTTPException(403, '手机连接尚未配置或访问地址不匹配。请使用电脑上显示的收件箱地址。')


def same_origin(request):
    if (request.headers.get('origin') != origin_for(request)
            or request.headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'}):
        raise HTTPException(403, '请在收件箱页面内提交。')


def limited(identity, count, seconds):
    with attempt_lock:
        stamp = time.monotonic()
        history = attempts[identity]
        while history and history[0] < stamp - seconds:
            history.popleft()
        if len(history) >= count:
            raise HTTPException(429, '操作过于频繁，请稍后再试。', headers={'Retry-After': str(seconds)})
        history.append(stamp)


def local_admin(request: Request, key=Depends(admin)):
    if not direct_local_access(request):
        raise HTTPException(403, '请直接在电脑上管理手机配对。')
    return key


def access(request: Request, db=Depends(get_db)):
    origin_for(request)
    if direct_local_access(request) and request.cookies.get('atlas_session'):
        key = authenticate(request, db)
        if key.scope == 'admin':
            return key
    token = request.cookies.get(COOKIE, '')
    key = db.scalar(select(Key).where(Key.key_hash == digest(token), Key.kind == 'inbox_device',
                                     Key.scope == 'inbox', Key.revoked_at.is_(None))) if token else None
    if not key or not key.expires_at or utc(key.expires_at) <= now():
        raise HTTPException(401, '请先用电脑上的配对码连接这台 iPhone。')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        same_origin(request)
        csrf = request.headers.get('x-csrf-token', '')
        if not csrf or not hmac.compare_digest(key.csrf_hash or '', digest(csrf)):
            raise HTTPException(403, '页面验证已失效，请刷新收件箱。')
    request.state.actor = 'inbox:' + key.id
    return key


def audit(db, key, action, target=None):
    db.add(Audit(actor='inbox:' + key.id, action=action, target_type='inbox', target_id=target))


@admin_router.post('/pairings')
def create_pairing(response: Response, key=Depends(local_admin), db=Depends(get_db)):
    limited('create:' + key.id, 6, 60)
    db.execute(update(Key).where(Key.kind == 'inbox_pair', Key.revoked_at.is_(None)).values(revoked_at=now()))
    code = ''.join(secrets.choice('0123456789') for _ in range(8))
    # Pairing codes use a separate hash namespace from device credentials.
    pair = Key(name='iPhone 配对', key_hash=digest('inbox-pair:' + code), kind='inbox_pair', scope='inbox',
               expires_at=now() + timedelta(minutes=10))
    db.add(pair)
    db.flush()
    audit(db, key, 'inbox_pairing_created', pair.id)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'code': code, 'expires_at': pair.expires_at.isoformat(),
            'phone_url': connection_url() + '/inbox' if connection_url() else None}


@admin_router.get('/devices')
def devices(response: Response, key=Depends(local_admin), db=Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    rows = db.scalars(select(Key).where(Key.kind == 'inbox_device').order_by(Key.created_at.desc()))
    return {'phone_url': connection_url() + '/inbox' if connection_url() else None,
            'items': [{'id': row.id, 'name': row.name, 'created_at': utc(row.created_at).isoformat(),
                       'expires_at': utc(row.expires_at).isoformat(), 'revoked': bool(row.revoked_at)} for row in rows]}


@admin_router.post('/devices/{identity}/revoke')
def revoke(identity: str, key=Depends(local_admin), db=Depends(get_db)):
    device = db.get(Key, identity)
    if not device or device.kind != 'inbox_device':
        raise HTTPException(404, '手机配对不存在')
    device.revoked_at = now()
    audit(db, key, 'inbox_device_revoked', device.id)
    db.commit()
    return {'status': 'revoked'}


class Pairing(BaseModel):
    code: str = Field(pattern=r'^\d{8}$')
    name: str = Field(default='我的 iPhone', min_length=1, max_length=50)


@router.post('/pair')
def pair(payload: Pairing, request: Request, response: Response, db=Depends(get_db)):
    same_origin(request)
    address = request.client.host if request.client else 'unknown'
    limited('pair-global', 32, 300)
    limited('pair:' + address, 8, 300)
    row = db.scalar(select(Key).where(Key.kind == 'inbox_pair', Key.key_hash == digest('inbox-pair:' + payload.code),
                                     Key.revoked_at.is_(None), Key.expires_at > now()))
    if not row:
        raise HTTPException(401, '配对码无效或已过期，请在电脑上生成新码。')
    consumed = db.execute(update(Key).where(Key.id == row.id, Key.revoked_at.is_(None), Key.expires_at > now())
                          .values(revoked_at=now()).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, '该配对码已使用，请在电脑上生成新码。')
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    device = Key(name=payload.name.strip() or '我的 iPhone', key_hash=digest(token), csrf_hash=digest(csrf),
                 kind='inbox_device', scope='inbox', expires_at=now() + timedelta(days=DEVICE_DAYS))
    db.add(device)
    db.flush()
    audit(db, device, 'inbox_device_paired', device.id)
    db.commit()
    secure = not direct_local_access(request)
    for name, value, http_only in [(COOKIE, token, True), (CSRF_COOKIE, csrf, False)]:
        response.set_cookie(name, value, httponly=http_only, secure=secure, samesite='strict', max_age=DEVICE_DAYS*86400, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'name': device.name, 'local': False, 'expires_at': device.expires_at.isoformat()}


@router.get('/me')
def me(response: Response, key=Depends(access)):
    response.headers['Cache-Control'] = 'no-store'
    return {'name': '本机' if key.kind != 'inbox_device' else key.name,
            'local': key.kind != 'inbox_device', 'expires_at': utc(key.expires_at).isoformat()}


@router.post('/disconnect')
def disconnect(response: Response, key=Depends(access), db=Depends(get_db)):
    if key.kind == 'inbox_device':
        key.revoked_at = now()
        audit(db, key, 'inbox_device_disconnected', key.id)
        db.commit()
    response.delete_cookie(COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return {'status': 'disconnected'}


class Capture(BaseModel):
    share_text: str = Field(min_length=1, max_length=8000)


@router.post('/submissions')
def submit(payload: Capture, request: Request, key=Depends(access), db=Depends(get_db)):
    limited('submit:' + key.id, 10, 60)
    result = enqueue_link_capture(db, payload.share_text)
    if not db.scalar(select(InboxSubmission).where(InboxSubmission.device_id == key.id,
                                                  InboxSubmission.work_id == result['work_id'])):
        try:
            with db.begin_nested():
                db.add(InboxSubmission(device_id=key.id, work_id=result['work_id']))
                db.flush()
        except IntegrityError:
            pass
    audit(db, key, 'inbox_link_submitted', result['work_id'])
    db.commit()
    return result


def visible(key):
    statement = select(InboxSubmission, Work, Creator).join(Work, Work.id == InboxSubmission.work_id).join(Creator, Creator.id == Work.creator_id)
    return statement if key.kind != 'inbox_device' else statement.where(InboxSubmission.device_id == key.id)


def work_record(db, submission, work, creator):
    analysis = db.get(Analysis, work.current_analysis_id) if work.current_analysis_id else None
    complete = work.processing_state == 'completed' and analysis and analysis.status == 'success'
    count = db.scalar(select(func.count()).select_from(Knowledge).where(Knowledge.work_id == work.id,
                       Knowledge.analysis_id == work.current_analysis_id)) if complete else 0
    jobs = list(db.scalars(select(Job).where(Job.work_id == work.id).order_by(Job.updated_at.desc())))
    sync = next((j for j in jobs if j.job_type == 'brain_sync' and j.payload.get('analysis_id') == work.current_analysis_id), None)
    current = next((j for j in jobs if j.job_type in {'capture', 'analyze'} and j.state == 'running'), None)
    problem = next((j for j in jobs if j.job_type in {'capture', 'analyze'} and j.state in {'waiting', 'blocked', 'exhausted'}), None) if not complete else None
    stage = {'pending_capture': '等待采集素材', 'capturing': '采集作品素材', 'pending_analysis': '等待解析',
             'analyzing': '转写与理解内容', 'completed': '知识已入库', 'failed_retryable': '学习暂停'}.get(work.processing_state, '等待处理')
    if current and current.job_type == 'analyze':
        context = work.media_context or {}
        stage = '提取知识与建立索引' if 'ocr_segments' in context else '理解图片与视频画面' if 'transcript_segments' in context or work.content_type == 'image' else '转写视频音轨'
    source = 'https://www.douyin.com/' + ('note/' if work.content_type == 'image' else 'video/') + str(work.platform_work_id)
    return {'id': work.id, 'submission_id': submission.id, 'title': work.title, 'creator': creator.display_name,
            'source_url': source, 'state': work.processing_state, 'stage': stage,
            'submitted_at': utc(submission.created_at).isoformat(), 'knowledge_count': count,
            'capture_done': work.processing_state in {'pending_analysis', 'analyzing', 'completed'} or any(j.job_type == 'analyze' for j in jobs),
            'knowledge_done': bool(complete), 'brain_state': sync.state if sync else 'not_started',
            'error': problem.last_error_message if problem else None,
            'next_retry_at': utc(problem.next_retry_at).isoformat() if problem and problem.next_retry_at else None}


@router.get('/submissions')
def submissions(response: Response, offset: int=0, key=Depends(access), db=Depends(get_db)):
    statement = visible(key)
    response.headers['Cache-Control'] = 'no-store'
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(statement.order_by(InboxSubmission.created_at.desc()).offset(max(0, offset)).limit(20))
    return {'items': [work_record(db, *row) for row in rows], 'total': total, 'offset': max(0, offset)}


@router.get('/submissions/{identity}')
def detail(identity: str, response: Response, key=Depends(access), db=Depends(get_db)):
    row = db.execute(visible(key).where(Work.id == identity).order_by(InboxSubmission.created_at.desc())).first()
    if not row:
        raise HTTPException(404, '这条作品未通过当前收件箱提交。')
    submission, work, creator = row
    result = work_record(db, submission, work, creator)
    response.headers['Cache-Control'] = 'no-store'
    result['knowledge'] = [{'id': item.id, 'title': item.title, 'summary': item.content_zh,
                            'evidence': item.content_original, 'claim_kind': item.claim_kind}
                           for item in db.scalars(select(Knowledge).where(Knowledge.work_id == work.id,
                               Knowledge.analysis_id == work.current_analysis_id).order_by(Knowledge.created_at))] if result['knowledge_done'] else []
    return result
