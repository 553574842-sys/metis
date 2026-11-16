import csv
import io
import secrets
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from PIL import Image
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from .config import settings, ROOT
from .db import get_db, Session, now, utc
from .models import Creator, Scan, Work, Asset, Analysis, Segment, Knowledge, Relation, Job, Attempt, Alert, Report, Backup, Key, Principal, Audit, ServiceHeartbeat
from .schemas import CreatorInput, Question, Comparison
from .security import authenticate, admin, initialize_admin, create_session, password_matches, digest, direct_local_access
from .adapters import parse_profile, safe_url, capabilities, ProcessingError
from .pipeline import enqueue, enqueue_scan, enqueue_link_capture
from .jobs import local_supervisor
from .knowledge import search, ask, compare, visual_record, visual_records
from .media import free_space, check_space, under, add_asset


def record(row, exclude=()):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns if col.name not in exclude}


def audit(db, request, action, target_type, target_id=None):
    db.add(Audit(actor=getattr(request.state, 'actor', 'admin'), action=action, target_type=target_type, target_id=target_id))


def get_or_404(db, model, identity):
    row = db.get(model, identity)
    if not row:
        raise HTTPException(404, '记录不存在')
    return row


@asynccontextmanager
async def lifespan(app):
    with Session() as db:
        initialize_admin(db)
    stop = threading.Event()
    thread = None
    if settings.task_mode == 'local':
        thread = threading.Thread(target=local_supervisor, args=(stop,), daemon=True)
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=10)


app = FastAPI(title='Metis · 创作者知识库', version='1.0.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins.split(','), allow_credentials=True,
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Authorization', 'X-CSRF-Token'])


@app.middleware('http')
async def request_id(request, call_next):
    request.state.request_id = str(uuid4())
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse({'error': {'code': 'INTERNAL_ERROR', 'message': '服务暂时不可用，请检查系统运行监控。',
                                           'retryable': True, 'request_id': request.state.request_id}}, status_code=500)
    response.headers['X-Request-ID'] = request.state.request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
    return response


@app.exception_handler(HTTPException)
async def http_error(request, error):
    return JSONResponse({'error': {'code': f'HTTP_{error.status_code}', 'message': error.detail,
                                  'retryable': False, 'request_id': request.state.request_id}}, status_code=error.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # 不回显上传内容、密码或未经白名单过滤的输入字段。
    fields = ', '.join('.'.join(str(x) for x in e['loc']) for e in error.errors())
    return JSONResponse({'error': {'code': 'INVALID_INPUT', 'message': '输入校验失败：' + fields,
                                  'retryable': False, 'request_id': request.state.request_id}}, status_code=422)


@app.exception_handler(ProcessingError)
async def processing_error(request, error):
    return JSONResponse({'error': {'code': error.code, 'message': error.message,
                                  'retryable': error.retryable, 'request_id': request.state.request_id}}, status_code=409)


@app.get('/health/live')
@app.get('/api/v1/health/live')
def live():
    return {'status': 'alive', **({'reflection_model': settings.reflection_text_model}
            if settings.reflection_enabled and settings.reflection_provider == 'local'
            and settings.reflection_base_url.rstrip('/') == 'http://127.0.0.1:1234/v1' else {})}


@app.get('/health/ready', dependencies=[Depends(admin)])
def ready(db=Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ready'}


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=500)


login_attempts = defaultdict(deque)
login_lock = threading.Lock()


@app.post('/api/v1/auth/local')
def local_login(request: Request, response: Response, db=Depends(get_db)):
    if not direct_local_access(request):
        raise HTTPException(403, '本机免密入口仅限直接在本机访问')
    token, csrf = create_session(db, local=True)
    response.set_cookie('atlas_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', max_age=43200)
    response.set_cookie('atlas_csrf', csrf, secure=settings.cookie_secure, samesite='strict', max_age=43200)
    request.state.actor = 'local'
    audit(db, request, 'local_session', 'session')
    db.commit()
    return {'name': 'local', 'csrf_token': csrf}


@app.post('/api/v1/auth/login')
def login(payload: Login, request: Request, response: Response, db=Depends(get_db)):
    origin = request.headers.get('origin')
    if origin and origin not in settings.allowed_origins.split(','):
        raise HTTPException(403, '登录来源未获授权')
    address = request.client.host if request.client else 'unknown'
    with login_lock:
        if len(login_attempts) > 1024:
            login_attempts.clear()
        attempts = login_attempts[address]
        while attempts and attempts[0] < now()-timedelta(minutes=5):
            attempts.popleft()
        if len(attempts) >= 5:
            raise HTTPException(429, '登录尝试过多，请 5 分钟后重试')
        attempts.append(now())
    principal = db.scalar(select(Principal).where(Principal.name == 'admin'))
    if not principal or not password_matches(payload.password, principal.password_hash):
        raise HTTPException(401, '密码不正确')
    token, csrf = create_session(db)
    response.set_cookie('atlas_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', max_age=43200)
    response.set_cookie('atlas_csrf', csrf, secure=settings.cookie_secure, samesite='strict', max_age=43200)
    with login_lock:
        login_attempts[address].clear()
    audit(db, request, 'login', 'session')
    db.commit()
    return {'name': 'admin', 'csrf_token': csrf}


api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])


@api.get('/auth/me')
def me(request: Request, key=Depends(authenticate)):
    return {'name': key.name, 'scope': key.scope, 'source_mode': settings.ai_provider, 'timezone': settings.app_timezone,
            'local_passwordless': direct_local_access(request)}


@api.post('/auth/logout')
def logout(response: Response, db=Depends(get_db), key=Depends(authenticate)):
    key.revoked_at = now()
    db.merge(key)
    db.commit()
    response.delete_cookie('atlas_session')
    response.delete_cookie('atlas_csrf')
    return {'status': 'logged_out'}


def add_creator(db, payload):
    try:
        parsed = parse_profile(payload.profile_url, payload.platform)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    existing = db.scalar(select(Creator).where(Creator.normalized_profile_url == parsed['normalized_profile_url']))
    if existing:
        return existing, False
    mode = settings.adapter_douyin_mode if parsed['platform'] == 'douyin' else settings.adapter_xhs_mode
    if mode == 'mock' and urlsplit(parsed['normalized_profile_url']).hostname != 'example.invalid':
        mode = 'real'
    if mode == 'browser':
        mode = 'real'
    creator = Creator(**parsed, profile_url=payload.profile_url,
                      display_name=payload.display_name or ('模拟账号 · ' if mode == 'mock' else '')+parsed['platform_creator_id'][:18],
                      source_mode=mode, next_check_at=now()+timedelta(hours=24))
    db.add(creator)
    try:
        db.flush()
        enqueue_scan(db, creator, 'history')
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Creator).where(Creator.normalized_profile_url == parsed['normalized_profile_url']))
        if existing:
            return existing, False
        raise HTTPException(409, '账号标识已存在，请使用已有账号') from None
    return creator, True


@api.post('/creators', dependencies=[Depends(admin)])
def creator_add(payload: CreatorInput, request: Request, db=Depends(get_db)):
    creator, added = add_creator(db, payload)
    audit(db, request, 'creator_add' if added else 'creator_duplicate', 'creator', creator.id)
    db.commit()
    return {'creator': record(creator), 'added': added}


class Batch(BaseModel):
    text: str = Field(max_length=200000)


@api.post('/creators/import', dependencies=[Depends(admin)])
def creator_import(payload: Batch, request: Request, db=Depends(get_db)):
    value = payload.text.lstrip('\ufeff').strip()
    first = value.splitlines()[0] if value else ''
    rows = list(csv.DictReader(io.StringIO(value))) if 'profile_url' in first else [{'profile_url': line.strip()} for line in value.splitlines()]
    if len(rows) > 500:
        raise HTTPException(422, '一次最多导入 500 行')
    result = []
    for number, row in enumerate(rows, 1):
        try:
            creator, added = add_creator(db, CreatorInput(profile_url=row.get('profile_url', ''),
                display_name=row.get('display_name', '') or '', platform=row.get('platform') or None))
            result.append({'row': number, 'status': 'added' if added else 'duplicate', 'creator_id': creator.id})
        except (HTTPException, ValueError) as error:
            db.rollback()
            result.append({'row': number, 'status': 'invalid', 'message': error.detail if isinstance(error, HTTPException) else '输入字段无效'})
    audit(db, request, 'creator_batch_import', 'creator')
    db.commit()
    return {'rows': result, 'added': sum(x['status']=='added' for x in result)}


@api.get('/creators')
def creator_list(db=Depends(get_db)):
    creators = db.scalars(select(Creator).order_by(Creator.created_at.desc())).all()
    return {'items': [{**record(x), 'work_count': db.scalar(select(func.count()).select_from(Work).where(Work.creator_id == x.id))} for x in creators]}


@api.get('/creators/{identity}')
def creator_detail(identity: str, db=Depends(get_db)):
    return record(get_or_404(db, Creator, identity))


@api.post('/creators/{identity}/stop', dependencies=[Depends(admin)])
def creator_stop(identity: str, request: Request, db=Depends(get_db)):
    creator = get_or_404(db, Creator, identity)
    creator.tracking_state, creator.next_check_at = 'stopped', None
    db.execute(Job.__table__.update().where(Job.creator_id == identity, Job.job_type.in_(['scan', 'capture']), Job.state.in_(['queued','waiting','exhausted'])).values(state='canceled'))
    db.execute(Scan.__table__.update().where(Scan.creator_id == identity, Scan.status.in_(['queued','waiting'])).values(status='canceled'))
    audit(db, request, 'stop_tracking_keep_history', 'creator', identity)
    db.commit()
    return {'status': 'stopped', 'history_retained': True}


@api.post('/creators/{identity}/resume', dependencies=[Depends(admin)])
def creator_resume(identity: str, request: Request, db=Depends(get_db)):
    creator = get_or_404(db, Creator, identity)
    release_unconfigured_jobs(db, creator)
    creator.tracking_state, creator.next_check_at = 'tracking', now()
    for job in db.scalars(select(Job).where(Job.creator_id == identity, Job.state == 'canceled', Job.job_type == 'capture')):
        job.state, job.next_retry_at = 'queued', now()
    enqueue_scan(db, creator, 'history' if creator.history_scan_state == 'pending' else 'incremental')
    audit(db, request, 'resume_tracking', 'creator', identity)
    db.commit()
    return record(creator)


def release_unconfigured_jobs(db, creator):
    if creator.platform == 'douyin' and settings.platform_requests_enabled and settings.adapter_douyin_mode == 'browser':
        for job in db.scalars(select(Job).where(Job.creator_id == creator.id, Job.state == 'blocked',
                                               Job.last_error_code == 'ACCESS_NOT_CONFIGURED')):
            job.state = 'canceled'


@api.post('/creators/{identity}/scan', dependencies=[Depends(admin)])
def creator_scan(identity: str, request: Request, scan_type: str='incremental', db=Depends(get_db)):
    creator = get_or_404(db, Creator, identity)
    if creator.tracking_state != 'tracking':
        raise HTTPException(409, '请先恢复跟踪')
    if scan_type not in {'history','incremental'}:
        raise HTTPException(422, '扫描类型无效')
    release_unconfigured_jobs(db, creator)
    active = db.scalar(select(Job).where(Job.creator_id == identity, Job.job_type == 'scan', Job.state == 'blocked'))
    if active:
        raise HTTPException(409, '平台访问受限；请先完成真实接入配置。')
    scan = enqueue_scan(db, creator, scan_type)
    audit(db, request, 'request_scan', 'creator', identity)
    db.commit()
    return record(scan)


@api.get('/creators/{identity}/scans')
def scans(identity: str, db=Depends(get_db)):
    get_or_404(db, Creator, identity)
    return {'items': [record(x) for x in db.scalars(select(Scan).where(Scan.creator_id == identity).order_by(Scan.created_at.desc()).limit(100))]}


@api.get('/works')
def works(q: str='', creator_id: str='', platform: str='', state: str='', date_from: str='', date_to: str='',
          limit: int=30, offset: int=0, db=Depends(get_db)):
    query = select(Work, Creator.display_name).join(Creator, Work.creator_id == Creator.id)
    for value, col in [(creator_id, Work.creator_id), (platform, Work.platform), (state, Work.processing_state)]:
        if value:
            query = query.where(col == value)
    if q:
        query = query.where(Work.title.contains(q) | Work.body_text.contains(q))
    if date_from:
        query = query.where(Work.published_at >= datetime.fromisoformat(date_from).replace(tzinfo=ZoneInfo(settings.app_timezone)))
    if date_to:
        query = query.where(Work.published_at < datetime.fromisoformat(date_to).replace(tzinfo=ZoneInfo(settings.app_timezone))+timedelta(days=1))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Work.discovered_at.desc()).limit(max(1,min(limit,100))).offset(max(0,offset)))
    return {'items': [{**record(x, ('temporary_video','media_context')), 'creator': name,
                      'analysis_mode': x.media_context.get('provider', 'pending')} for x, name in rows], 'total': total}


@api.get('/works/{identity}')
def work_detail(identity: str, db=Depends(get_db)):
    work = get_or_404(db, Work, identity)
    run = db.get(Analysis, work.current_analysis_id) if work.current_analysis_id else None
    return {**record(work, ('temporary_video','media_context')), 'creator': db.get(Creator, work.creator_id).display_name,
            'assets': [{**record(x, ('file_path',)), 'url': f'/api/v1/assets/{x.id}'} for x in db.scalars(select(Asset).where(Asset.work_id==identity).order_by(Asset.asset_type, Asset.source_order))],
            'analysis': record(run) if run else None, 'temporary_video_present': bool(work.temporary_video),
            'segments': [record(x) for x in db.scalars(select(Segment).where(Segment.analysis_id==work.current_analysis_id).order_by(Segment.segment_type, Segment.sequence_no))],
            'knowledge_items': [record(x) for x in db.scalars(select(Knowledge).where(Knowledge.analysis_id==work.current_analysis_id))],
            'jobs': [record(x, ('lease_token','payload')) for x in db.scalars(select(Job).where(Job.work_id==identity))],
            'analysis_history': [record(x, ('output',)) for x in db.scalars(select(Analysis).where(Analysis.work_id==identity).order_by(Analysis.created_at.desc()))]}


@api.get('/assets/{identity}')
def asset_file(identity: str, db=Depends(get_db)):
    asset = get_or_404(db, Asset, identity)
    try:
        path = under(settings.data_dir, settings.data_dir / asset.file_path)
    except ValueError:
        raise HTTPException(403, '素材路径无效') from None
    if not path.is_file():
        raise HTTPException(404, '素材文件不存在')
    return FileResponse(path)


class CaptureLink(BaseModel):
    share_text: str = Field(min_length=1, max_length=8000)


@api.post('/works/capture-link', dependencies=[Depends(admin)])
def work_capture_link(payload: CaptureLink, request: Request, db=Depends(get_db)):
    result = enqueue_link_capture(db, payload.share_text)
    audit(db, request, 'capture_link_existing' if result['existing'] else 'capture_link', 'work', result['work_id'])
    db.commit()
    return result


class NightBatchInput(BaseModel):
    creator_id: str
    maximum: int = Field(default=10, ge=1, le=10)


@api.post('/night-batches', dependencies=[Depends(admin)])
def night_batch_start(payload: NightBatchInput, request: Request, db=Depends(get_db)):
    from .night_batch import create, summary
    batch = create(db, get_or_404(db, Creator, payload.creator_id), payload.maximum)
    audit(db, request, 'start_bounded_night_batch', 'job', batch.id)
    db.commit()
    return summary(db, batch)


@api.get('/night-batches')
def night_batches(db=Depends(get_db)):
    from .night_batch import summary, guardian_awake
    batches = db.scalars(select(Job).where(Job.job_type == 'night_batch').order_by(Job.created_at.desc()).limit(5))
    return {'items': [{**summary(db, batch), 'sleep_prevention_active': guardian_awake(batch)} for batch in batches]}


@api.post('/night-batches/{identity}/stop', dependencies=[Depends(admin)])
def night_batch_stop(identity: str, request: Request, db=Depends(get_db)):
    from .night_batch import stop, summary
    batch = get_or_404(db, Job, identity)
    if batch.job_type != 'night_batch':
        raise HTTPException(404, '批次不存在')
    stop(db, batch)
    audit(db, request, 'stop_bounded_night_batch', 'job', batch.id)
    db.commit()
    return summary(db, batch)


@api.post('/works/import', dependencies=[Depends(admin)])
async def work_import(request: Request, platform: str=Form(...), title: str=Form(...), body_text: str=Form(''),
                      original_url: str=Form(''), rights_statement: str=Form(...), creator_id: str=Form(''),
                      platform_work_id: str=Form(''), files: list[UploadFile]=File(default=[]), db=Depends(get_db)):
    if platform not in {'douyin','xiaohongshu'} or not title.strip() or len(title)>1000 or len(body_text)>200000 or len(files)>32 or len(rights_statement.strip())<5:
        raise HTTPException(422, '请提供平台、标题及至少 5 字的素材权利声明；一次最多 32 张图片或 1 个视频。')
    try:
        safe_url(original_url, True)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if platform_work_id and db.scalar(select(Work).where(Work.platform==platform, Work.platform_work_id==platform_work_id)):
        raise HTTPException(409, '该平台作品 ID 已存在')
    creator = db.get(Creator, creator_id) if creator_id else db.scalar(select(Creator).where(Creator.normalized_profile_url=='manual:'+platform))
    if creator_id and not creator:
        raise HTTPException(404, '创作者不存在')
    if creator and creator.platform != platform:
        raise HTTPException(422, '素材平台与账号不匹配')
    if not creator:
        creator = Creator(platform=platform, profile_url='', normalized_profile_url='manual:'+platform,
                          display_name='人工导入 · '+('抖音' if platform=='douyin' else '小红书'), source_mode='manual',
                          tracking_state='stopped', access_state='manual', history_scan_state='manual', next_check_at=None)
        db.add(creator)
        db.flush()
        creator.next_check_at = None
    work = Work(creator_id=creator.id, platform=platform, platform_work_id=platform_work_id or None,
                identity_unverified=not bool(platform_work_id), original_url=original_url, title=title, body_text=body_text,
                rights_statement=rights_statement, source_mode='manual', processing_state='pending_analysis',
                content_type='text', link_validation='not_checked' if original_url else 'missing', published_at=None)
    db.add(work)
    db.flush()
    written = []
    total_bytes = 0
    try:
        check_space()
        for order, upload in enumerate(files):
            suffix = Path(upload.filename or '').suffix.lower()
            video = suffix in {'.mp4','.mov','.webm','.mkv'}
            if suffix not in {'.png','.jpg','.jpeg','.webp','.mp4','.mov','.webm','.mkv'} or (video and len(files)>1):
                raise HTTPException(422, '接受 PNG/JPG/WebP 图片，或单个 MP4/MOV/WebM/MKV 视频，图片与视频请分别导入。')
            folder = settings.data_dir / ('temporary_video' if video else 'images') / work.id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('original'+suffix if video else str(order)+suffix)
            written.append(path)
            with path.open('wb') as output:
                while chunk := await upload.read(1024*1024):
                    total_bytes += len(chunk)
                    if total_bytes > settings.max_upload_mb*1024*1024:
                        raise HTTPException(413, '上传文件总量超过配置上限')
                    output.write(chunk)
            if video:
                work.temporary_video = path.relative_to(settings.data_dir).as_posix()
                work.content_type = 'video'
            else:
                with Image.open(path) as picture:
                    if picture.format not in {'PNG','JPEG','WEBP'}:
                        raise HTTPException(422, '图片实际格式无效')
                    picture.verify()
                add_asset(db, work, path, 'image', order)
                work.content_type = 'image'
        enqueue(db, f'analyze:{work.id}:initial', 'analyze', creator.id, work.id)
        audit(db, request, 'manual_import', 'work', work.id)
        db.commit()
    except Exception:
        db.rollback()
        for path in written:
            under(settings.data_dir, path).unlink(missing_ok=True)
        raise
    return {'work_id': work.id, 'source_mode': 'manual', 'processing_state': 'pending_analysis', 'source_missing': not bool(original_url)}


@api.post('/works/{identity}/reanalyze', dependencies=[Depends(admin)])
def reanalyze(identity: str, request: Request, db=Depends(get_db)):
    work = get_or_404(db, Work, identity)
    jobs = db.scalars(select(Job).where(Job.work_id==identity, Job.job_type=='analyze')
                      .order_by(Job.created_at.desc(), Job.id.desc())).all()
    active = next((job for job in jobs if job.state in {'queued', 'running', 'waiting'}), None)
    if active:
        return {'job_id': active.id}
    key = f'analyze:{identity}:{work.version+1}:after:{jobs[0].id if jobs else "manual"}'
    try:
        with db.begin_nested():
            job = enqueue(db, key, 'analyze', work.creator_id, work.id, {'reanalysis': True})
    except IntegrityError:
        job = db.scalar(select(Job).where(Job.idempotency_key == key))
        if not job:
            raise
    for previous in jobs:
        if previous.state in {'exhausted', 'blocked'}:
            previous.state, previous.next_retry_at = 'canceled', None
    if work.processing_state == 'failed_retryable':
        work.processing_state = 'pending_analysis'
    audit(db, request, 'reanalyze_saved_materials', 'work', work.id)
    db.commit()
    return {'job_id': job.id, 'limitation': '原视频已删除后，仅使用保存的转写、原图、关键帧。'}


@api.get('/knowledge/search')
def knowledge_search(q: str='', platform: str='', creator_id: str='', date_from: str='', date_to: str='', topic: str='',
                     limit: int=20, offset: int=0, tag: str='', sort: Literal['relevance', 'newest', 'oldest']='relevance',
                     prefer_exact: bool=False, db=Depends(get_db)):
    from .knowledge_tags import LABELS
    if tag and tag not in LABELS:
        raise HTTPException(422, '知识标签无效')
    try:
        return search(db, q[:4000], {'platforms': [platform] if platform else [], 'creator_ids': [creator_id] if creator_id else [],
                                    'date_from': date_from, 'date_to': date_to, 'topic': topic,
                                    'tags': [tag] if tag else []}, max(1,min(limit,100)), max(0,offset), sort, prefer_exact=prefer_exact)
    except ValueError:
        raise HTTPException(422, '日期格式无效') from None


@api.get('/knowledge/tags')
def knowledge_tags(db=Depends(get_db)):
    from .knowledge_tags import catalog
    return catalog(db)


@api.get('/knowledge/items/{identity}')
def knowledge_item(identity: str, db=Depends(get_db)):
    from .knowledge_tags import labels
    if identity.startswith('image:'):
        hit = next(visual_records(db, {}, asset_id=identity.split(':', 1)[1]), None)
        if hit is None:
            raise HTTPException(404, '记录不存在')
        item = visual_record(*hit, 1.0)
        return {**item, 'id': identity, 'content_zh': item['summary'], 'content_original': item['evidence_excerpt'],
                'segments': [record(get_or_404(db, Segment, segment_id)) for segment_id in item['source_segment_ids']]}
    if identity.startswith('reflection:'):
        from .models import LearningReflection
        from .reflections import evidence_record as reflection_evidence
        row = get_or_404(db, LearningReflection, identity.split(':', 1)[1])
        if row.status != 'completed':
            raise HTTPException(409, '深入学习尚未完成')
        work = db.get(Work, row.work_id)
        item = reflection_evidence(row, work, db.get(Creator, work.creator_id))
        return {**item, 'id': identity, 'content_zh': item['summary'], 'content_original': item['evidence_excerpt'],
                'segments': [record(get_or_404(db, Segment, identity)) for identity in item['source_segment_ids']]}
    item = get_or_404(db, Knowledge, identity)
    work = db.get(Work, item.work_id)
    return {**record(item), 'source_url': work.original_url, 'source_mode': work.source_mode,
            'tags': labels(item.title, item.content_zh, work.title), 'learned_at': utc(item.created_at).isoformat(),
            'segments': [record(get_or_404(db, Segment, identity)) for identity in item.source_segment_ids]}


@api.get('/learning/context', dependencies=[Depends(admin)])
def learning_context():
    from .reflections import context_snapshot
    return context_snapshot()


from .reflections import LearningContext


@api.post('/learning/context', dependencies=[Depends(admin)])
def learning_context_save(payload: LearningContext, request: Request, db=Depends(get_db)):
    from .reflections import save_context
    result = save_context(payload)
    audit(db, request, 'update_learning_context', 'learning_context')
    db.commit()
    return result


@api.get('/works/{identity}/reflection')
def work_reflection(identity: str, db=Depends(get_db)):
    from .models import LearningReflection
    from .reflections import detail
    work = get_or_404(db, Work, identity)
    row = db.scalar(select(LearningReflection).where(LearningReflection.work_id == work.id,
        LearningReflection.analysis_id == work.current_analysis_id).order_by(LearningReflection.created_at.desc()).limit(1))
    return {'enabled': settings.reflection_enabled, 'mode': settings.reflection_provider,
            'reflection': detail(db, row) if row else None}


@api.post('/works/{identity}/reflection', dependencies=[Depends(admin)])
def work_reflection_create(identity: str, request: Request, db=Depends(get_db)):
    from .reflections import enqueue_reflection, detail
    row, job = enqueue_reflection(db, get_or_404(db, Work, identity))
    audit(db, request, 'deepen_learning', 'reflection', row.id)
    db.commit()
    return {'job_id': job.id, 'reflection': detail(db, row)}


@api.post('/knowledge/ask')
def knowledge_ask(payload: Question, db=Depends(get_db)):
    return ask(db, payload.question, payload.filters)


@api.post('/knowledge/compare')
def knowledge_compare(payload: Comparison, db=Depends(get_db)):
    return compare(db, payload.question, payload.creator_ids, payload.filters)


@api.get('/knowledge/relations')
def relations(item_id: str='', db=Depends(get_db)):
    query = select(Relation)
    if item_id:
        query = query.where((Relation.source_item_id==item_id)|(Relation.target_item_id==item_id))
    rows = db.scalars(query.order_by(Relation.created_at.desc()).limit(100)).all()
    result = []
    for row in rows:
        left, right = db.get(Knowledge,row.source_item_id), db.get(Knowledge,row.target_item_id)
        if left.analysis_id != db.get(Work,left.work_id).current_analysis_id or right.analysis_id != db.get(Work,right.work_id).current_analysis_id:
            continue
        result.append({**record(row), 'source_title': left.title, 'target_title': right.title,
                       'source_url': db.get(Work,left.work_id).original_url, 'target_url': db.get(Work,right.work_id).original_url})
    return {'items': result}


@api.get('/brain/status')
def brain_status(db=Depends(get_db)):
    from .brain_sync import status
    return status(db)


@api.post('/brain/sync', dependencies=[Depends(admin)])
def brain_sync(request: Request, db=Depends(get_db)):
    from .brain_sync import enqueue_all
    result = enqueue_all(db)
    audit(db, request, 'sync_knowledge_to_local_brain', 'brain')
    db.commit()
    return result


@api.get('/reports/daily')
def reports(db=Depends(get_db)):
    return {'items': [record(x) for x in db.scalars(select(Report).order_by(Report.report_date.desc()).limit(90))]}


@api.get('/reports/daily/{date}')
def report_get(date: str, db=Depends(get_db)):
    row = db.scalar(select(Report).where(Report.report_date==date, Report.timezone==settings.app_timezone))
    if not row:
        raise HTTPException(404, '当天报告尚未生成')
    return record(row)


@api.post('/reports/daily/{date}/generate', dependencies=[Depends(admin)])
def report_generate(date: str, request: Request, db=Depends(get_db)):
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(422, '报告日期无效') from None
    if date > now().astimezone(ZoneInfo(settings.app_timezone)).date().isoformat():
        raise HTTPException(422, '不能生成未来日期的报告')
    job = db.scalar(select(Job).where(Job.idempotency_key==f'report:{settings.app_timezone}:{date}'))
    if job and job.state in {'queued','waiting','running'}:
        return {'job_id': job.id}
    if job:
        job.state, job.next_retry_at, job.attempt_count = 'queued', now(), 0
    else:
        job = enqueue(db, f'report:{settings.app_timezone}:{date}', 'report', payload={'date':date})
    audit(db, request, 'generate_daily_report', 'report', date)
    db.commit()
    return {'job_id': job.id}


@api.get('/overview')
def overview(db=Depends(get_db)):
    states = dict(db.execute(select(Work.processing_state,func.count()).group_by(Work.processing_state)).all())
    today = now().astimezone(ZoneInfo(settings.app_timezone)).date()
    midnight = datetime.combine(today, datetime.min.time(), tzinfo=ZoneInfo(settings.app_timezone))
    return {'creator_count': db.scalar(select(func.count()).select_from(Creator)),
            'tracking_count': db.scalar(select(func.count()).select_from(Creator).where(Creator.tracking_state=='tracking')),
            'work_count': sum(states.values()), 'states': states,
            'knowledge_count': db.scalar(select(func.count()).select_from(Knowledge).join(Work,Knowledge.work_id==Work.id).where(Knowledge.analysis_id==Work.current_analysis_id)),
            'today_knowledge_count': db.scalar(select(func.count()).select_from(Knowledge).where(Knowledge.created_at>=midnight)),
            'unread_alerts': db.scalar(select(func.count()).select_from(Alert).where(Alert.read_at.is_(None),Alert.resolved_at.is_(None))),
            'today': today.isoformat(), 'timezone': settings.app_timezone, 'mode': settings.ai_provider,
            'recent_knowledge': search(db,limit=5)['items'], 'recent_works': works(limit=5,db=db)['items']}


@api.get('/system/health', dependencies=[Depends(admin)])
def system_health(db=Depends(get_db)):
    db.execute(text('SELECT 1'))
    services = [{'name':'api','status':'healthy'}, {'name':'database','status':'healthy','detail':db.bind.dialect.name}]
    for name in ['scheduler','worker-capture','worker-analysis']:
        row = db.get(ServiceHeartbeat,name)
        status = 'healthy' if row and (now()-utc(row.seen_at)).total_seconds()<300 else 'unavailable'
        services.append({'name':name,'status':status,'last_seen_at':row.seen_at if row else None})
    import shutil
    services.append({'name':'ffmpeg','status':'healthy' if shutil.which('ffmpeg') else 'unavailable'})
    if settings.task_mode=='celery':
        import redis
        try:
            redis.Redis.from_url(settings.redis_url,socket_connect_timeout=2,socket_timeout=2).ping()
            services.append({'name':'redis','status':'healthy'})
        except Exception:
            services.append({'name':'redis','status':'unavailable'})
    configured = settings.ai_provider=='mock' or bool(settings.cloud_calls_enabled and settings.ai_api_key and settings.ai_text_model and settings.ai_vision_model and settings.ai_asr_model and settings.ai_embedding_model)
    if settings.ai_provider == 'minimax':
        configured = configured and (settings.ai_embedding_provider == 'local' or
                                     bool(settings.ai_embedding_base_url and settings.ai_embedding_api_key))
    elif settings.ai_provider == 'local':
        configured = settings.ai_embedding_provider == 'local' and all((settings.ai_text_model, settings.ai_vision_model,
                     settings.ai_asr_model, settings.ai_embedding_model))
    return {'services':services,'task_mode':settings.task_mode,'ai':{'provider':settings.ai_provider,'status':'mock' if settings.ai_provider=='mock' else 'configured_not_tested' if configured else 'waiting_configuration'},
            'report_schedule':{'hour':settings.report_hour,'minute':settings.report_minute,'timezone':settings.app_timezone},
            'platform_real_status':'not_tested'}


@api.get('/system/jobs', dependencies=[Depends(admin)])
def system_jobs(state: str='', db=Depends(get_db)):
    query = select(Job)
    if state:
        query = query.where(Job.state==state)
    return {'items': [record(x,('payload','lease_token')) for x in db.scalars(query.order_by(Job.updated_at.desc()).limit(150))]}


@api.get('/learning/status')
def learning_status(response: Response, run_id: str='', creator_id: str='', q: str='',
                    offset: int=0, limit: int=12, db=Depends(get_db)):
    from .learning import status
    response.headers['Cache-Control'] = 'no-store'
    return status(db, run_id, creator_id, q[:200], max(0, offset), min(30, max(1, limit)))


@api.get('/system/jobs/{identity}/attempts', dependencies=[Depends(admin)])
def attempts(identity: str, db=Depends(get_db)):
    return {'items':[record(x) for x in db.scalars(select(Attempt).where(Attempt.job_id==identity).order_by(Attempt.started_at))]}


@api.get('/system/alerts', dependencies=[Depends(admin)])
def system_alerts(db=Depends(get_db)):
    return {'items':[record(x) for x in db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(150))]}


@api.post('/system/alerts/{identity}/read', dependencies=[Depends(admin)])
def alert_read(identity: str, request: Request, resolve: bool=False, db=Depends(get_db)):
    alert = get_or_404(db,Alert,identity)
    alert.read_at = now()
    if resolve:
        alert.resolved_at = now()
    audit(db,request,'resolve_alert' if resolve else 'read_alert','alert',identity)
    db.commit()
    return record(alert)


@api.get('/system/storage', dependencies=[Depends(admin)])
def storage(db=Depends(get_db)):
    return {**free_space(),'asset_count':db.scalar(select(func.count()).select_from(Asset)),
            'permanent_bytes':db.scalar(select(func.coalesce(func.sum(Asset.byte_size),0))),
            'pending_video_cleanup':db.scalar(select(func.count()).select_from(Work).where(Work.temporary_video.is_not(None))),
            'backup_configured':settings.backup_dir.exists()}


@api.get('/system/backups', dependencies=[Depends(admin)])
def backups(db=Depends(get_db)):
    return {'items':[record(x,('target','manifest_path')) for x in db.scalars(select(Backup).order_by(Backup.started_at.desc()).limit(30))]}


@api.post('/system/backups', dependencies=[Depends(admin)])
def backup_start(request: Request, db=Depends(get_db)):
    active = db.scalar(select(Job).where(Job.job_type=='backup', Job.state.in_(['queued','running','waiting'])))
    if active:
        return {'job_id':active.id}
    job = enqueue(db,'backup:'+str(uuid4()),'backup')
    audit(db,request,'request_backup','backup',job.id)
    db.commit()
    return {'job_id':job.id}


@api.get('/system/adapters', dependencies=[Depends(admin)])
def adapters():
    return {'items':capabilities()}


class KeyInput(BaseModel):
    name: str = Field(min_length=1,max_length=100)


@api.get('/system/api-keys', dependencies=[Depends(admin)])
def api_keys(db=Depends(get_db)):
    return {'items':[record(x,('key_hash','csrf_hash')) for x in db.scalars(select(Key).where(Key.kind=='agent').order_by(Key.created_at.desc()))]}


@api.post('/system/api-keys', dependencies=[Depends(admin)])
def key_create(payload: KeyInput, request: Request, db=Depends(get_db)):
    value = 'atlas_'+secrets.token_urlsafe(40)
    key = Key(name=payload.name,key_hash=digest(value),kind='agent',scope='read')
    db.add(key)
    db.flush()
    audit(db,request,'create_readonly_api_key','api_key',key.id)
    db.commit()
    return {'id':key.id,'key':value,'scope':'read','message':'仅显示一次，请保存到你的密钥管理器。'}


@api.post('/system/api-keys/{identity}/revoke', dependencies=[Depends(admin)])
def key_revoke(identity: str, request: Request, db=Depends(get_db)):
    key = get_or_404(db,Key,identity)
    if key.kind!='agent':
        raise HTTPException(422,'只能撤销 Agent Key')
    key.revoked_at = now()
    audit(db,request,'revoke_api_key','api_key',identity)
    db.commit()
    return {'status':'revoked'}


@api.get('/openapi.json', dependencies=[Depends(admin)])
def openapi():
    return app.openapi()


app.include_router(api)
from .inbox import router as inbox_router, admin_router as inbox_admin_router
app.include_router(inbox_router)
app.include_router(inbox_admin_router)
dist = ROOT / 'frontend' / 'dist'
if (dist / 'assets').exists():
    app.mount('/assets',StaticFiles(directory=dist/'assets'),name='frontend_assets')


@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith(('api/','health/')):
        raise HTTPException(404,'接口不存在')
    if (dist/'index.html').is_file():
        return FileResponse(dist/'index.html')
    raise HTTPException(503,'前端尚未构建，请在 frontend 中执行 npm run build。')
