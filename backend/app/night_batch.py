"""Bounded local collection. A batch authorizes at most ten distinct new videos."""
import json
from datetime import datetime, timedelta

from sqlalchemy import select, func

from .adapters import ProcessingError, adapter_for
from .config import ROOT, settings
from .db import now, utc
from .models import Creator, Job, Knowledge, Work

HARD_PAUSE = {'PLATFORM_LOGIN_REQUIRED', 'PLATFORM_VERIFICATION_REQUIRED', 'PLATFORM_ACCESS_RESTRICTED',
              'PLATFORM_EMPTY_RESPONSE', 'PLATFORM_REQUEST_REJECTED', 'MEDIA_ACCESS_RESTRICTED'}
ACTIVE = {'queued', 'running', 'waiting'}


def deadline(batch):
    return utc(datetime.fromisoformat(batch.payload['deadline']))


def create(db, creator, maximum=10):
    from .pipeline import enqueue
    if type(maximum) is not int or not 1 <= maximum <= 10:
        raise ProcessingError('BATCH_LIMIT_INVALID', '本批次只允许 1–10 条视频。', False)
    if (creator.platform != 'douyin' or creator.source_mode != 'real' or creator.tracking_state != 'tracking'
            or not settings.platform_requests_enabled or settings.adapter_douyin_mode != 'browser'):
        raise ProcessingError('BATCH_SOURCE_INVALID', '请使用已启用本机抖音采集、正在跟踪的真实账号。', False)
    if settings.ai_provider != 'local' and (settings.ai_provider == 'mock' or not settings.cloud_calls_enabled):
        raise ProcessingError('BATCH_AI_NOT_ENABLED', '夜间真实解析需要已启用的模型配置。', False)
    for previous in db.scalars(select(Job).where(Job.job_type == 'night_batch', Job.creator_id == creator.id)):
        if deadline(previous) > now():
            if previous.payload['maximum'] != maximum:
                raise ProcessingError('BATCH_ALREADY_EXISTS', '该账号已有本夜批次；修改上限前请先查看其进度。', False)
            return previous  # Repeated requests cannot silently authorize ten more videos.
    if db.scalar(select(Job.id).where(Job.creator_id == creator.id, Job.job_type.in_(['scan', 'capture', 'analyze']), Job.state.in_(ACTIVE))):
        raise ProcessingError('BATCH_CREATOR_BUSY', '该账号已有未完成任务，请先完成或停止后再启动限量批次。', False)
    stamp = now()
    job = enqueue(db, f'night:{creator.id}:{stamp.isoformat()}', 'night_batch', creator.id,
                  payload={'maximum': maximum, 'deadline': (stamp + timedelta(hours=8)).isoformat(),
                           'selected_work_ids': [], 'selected_platform_ids': [], 'phase': 'selecting'})
    # This request authorizes a finite run, so do not let the daily scheduler
    # enqueue an unbounded scan behind it. Explicit resume can restore tracking.
    creator.next_check_at = None
    db.commit()
    return job


def select_videos(db, batch):
    from .pipeline import assert_owner, enqueue
    creator = db.get(Creator, batch.creator_id)
    if deadline(batch) <= now() or creator.tracking_state != 'tracking':
        batch.state = 'canceled'
        db.commit()
        return
    if batch.payload['selected_work_ids']:
        batch.state = 'completed'
        db.commit()
        return
    adapter = adapter_for('douyin', 'real')
    selected, seen, cursors = [], set(), {None}
    cursor = None
    end_reason = 'limit_reached'
    for _ in range(20):
        if deadline(batch) <= now():
            end_reason = 'time_limit'
            break
        page = adapter.list_creator_works(creator.platform_creator_id, cursor)
        for item in page['works']:
            if item.get('content_type') not in {'video', 'image'}:
                raise ProcessingError('BATCH_TYPE_UNVERIFIED', '列表缺少可验证的媒体类型，未自动入队。', False)
            identity = str(item['platform_work_id'])
            if identity in seen:
                continue
            seen.add(identity)
            if item.get('content_type') != 'video':
                continue
            if db.scalar(select(Work.id).where(Work.platform == 'douyin', Work.platform_work_id == identity)):
                continue
            selected.append(identity)
            if len(selected) == batch.payload['maximum']:
                break
        if len(selected) == batch.payload['maximum']:
            break
        cursor = page['next_cursor']
        if cursor is None:
            end_reason = 'platform_returned_end'
            break
        if cursor in cursors:
            raise ProcessingError('PLATFORM_CURSOR_STALLED', '批次分页游标重复，未继续请求。', False)
        cursors.add(cursor)
    else:
        end_reason = 'page_limit'
    assert_owner(db, batch)
    db.refresh(creator)
    if creator.tracking_state != 'tracking' or deadline(batch) <= now():
        batch.state = 'canceled'
        db.commit()
        return
    identifiers = []
    for index, identity in enumerate(selected):
        work = Work(creator_id=creator.id, platform='douyin', platform_work_id=identity,
                    title='等待获取视频内容', source_mode='real', content_type='video',
                    rights_statement='用户指定创作者的夜间限量公开作品采集，受平台实际访问条件约束。')
        db.add(work)
        db.flush()
        capture = enqueue(db, 'capture:' + work.id, 'capture', creator.id, work.id, {'batch_id': batch.id})
        capture.next_retry_at = now() + timedelta(seconds=60 * index)
        identifiers.append(work.id)
    batch.payload = {**batch.payload, 'selected_work_ids': identifiers, 'selected_platform_ids': selected,
                     'phase': 'processing', 'selection_end': end_reason}
    batch.state = 'completed'  # Selection finished; aggregate status comes from the child jobs.
    creator.access_state = 'available'
    db.commit()


def before_job(db, job):
    identity = job.payload.get('batch_id')
    if not identity:
        return True
    from .pipeline import assert_owner
    batch = db.get(Job, identity)
    creator = db.get(Creator, job.creator_id)
    assert_owner(db, job)
    valid = (batch and batch.job_type == 'night_batch' and job.work_id in batch.payload['selected_work_ids']
             and len(batch.payload['selected_work_ids']) <= batch.payload['maximum'] <= 10)
    if not valid or batch.state in {'blocked', 'canceled'} or deadline(batch) <= now() or creator.tracking_state != 'tracking':
        job.state = 'canceled'
        work = db.get(Work, job.work_id)
        if work and work.processing_state != 'completed':
            work.processing_state = 'canceled'
        db.commit()
        return False
    cooldown = batch.payload.get('cooldown_until')
    if (job.job_type == 'capture' or batch.payload.get('cooldown_stage') == 'all') and cooldown and utc(datetime.fromisoformat(cooldown)) > now():
        job.state, job.next_retry_at = 'waiting', utc(datetime.fromisoformat(cooldown))
        db.commit()
        return False
    return True


def on_failure(db, job, error):
    identity = job.payload.get('batch_id')
    if not identity:
        return
    batch = db.get(Job, identity)
    if error.code in HARD_PAUSE or (error.severe and error.code.startswith(('AI_', 'MINIMAX_'))):
        batch.state = 'blocked'
        batch.last_error_code, batch.last_error_message = error.code, error.message
        # Pending requests remain blocked until the user resolves the session.
        for child in db.scalars(select(Job).where(Job.work_id.in_(batch.payload['selected_work_ids']),
                                                  Job.job_type.in_(['capture', 'analyze']), Job.state.in_(['queued', 'waiting']))):
            child.state, child.next_retry_at = 'blocked', None
            child.last_error_code, child.last_error_message = 'BATCH_PAUSED', '平台访问条件变化，本批次已暂停。'
    elif error.code in {'PLATFORM_RATE_LIMITED', 'AI_HTTP_429'}:
        batch.payload = {**batch.payload, 'cooldown_until': (now() + timedelta(seconds=max(600, error.retry_after))).isoformat(),
                         'cooldown_stage': 'all' if error.code == 'AI_HTTP_429' else 'capture'}


def defer_brain(db, job):
    identity = job.payload.get('batch_id')
    if not identity:
        return False
    batch = db.get(Job, identity)
    if not batch or batch.state in {'blocked', 'canceled'} or deadline(batch) <= now():
        return False  # Let the execution guard cancel it instead of leaving it queued forever.
    return bool(db.scalar(select(Job.id).where(Job.work_id.in_(batch.payload['selected_work_ids']),
                                              Job.job_type.in_(['capture', 'analyze']), Job.state.in_(ACTIVE))))


def stop(db, batch):
    batch.state = 'canceled'
    for child in db.scalars(select(Job).where(Job.work_id.in_(batch.payload['selected_work_ids']),
                                              Job.job_type.in_(['capture', 'analyze', 'brain_sync']),
                                              Job.state.in_(['queued', 'waiting', 'blocked', 'exhausted']))):
        child.state = 'canceled'
        work = db.get(Work, child.work_id)
        if work.processing_state != 'completed':
            work.processing_state = 'canceled'
    db.commit()


def summary(db, batch):
    identifiers = batch.payload['selected_work_ids']
    works = list(db.scalars(select(Work).where(Work.id.in_(identifiers)))) if identifiers else []
    jobs = list(db.scalars(select(Job).where(Job.work_id.in_(identifiers)))) if identifiers else []
    complete = sum(work.processing_state == 'completed' for work in works)
    active = any(job.state in ACTIVE and job.job_type in {'capture', 'analyze'} for job in jobs)
    if batch.state == 'canceled':
        phase = 'stopped'
    elif batch.state == 'blocked':
        phase = 'paused'
    elif batch.state in ACTIVE:
        phase = 'selecting'
    elif deadline(batch) <= now() and complete < len(works):
        phase = 'time_limit'
    elif active:
        phase = 'processing'
    else:
        phase = 'finished'
    creator = db.get(Creator, batch.creator_id)
    knowledge = db.scalar(select(func.count()).select_from(Knowledge).join(Work, Work.id == Knowledge.work_id)
                          .where(Work.id.in_(identifiers), Knowledge.analysis_id == Work.current_analysis_id)) if identifiers else 0
    return {'id': batch.id, 'creator': creator.display_name, 'maximum': batch.payload['maximum'], 'deadline': batch.payload['deadline'],
            'phase': phase, 'selected': len(identifiers), 'completed': complete, 'knowledge_count': knowledge,
            'failed': sum(job.state in {'blocked', 'exhausted'} and job.job_type in {'capture', 'analyze'} for job in jobs),
            'brain_completed': sum(job.job_type == 'brain_sync' and job.state == 'completed' for job in jobs),
            'brain_pending': sum(job.job_type == 'brain_sync' and job.state in ACTIVE for job in jobs),
            'last_error_message': batch.last_error_message,
            'works': [{'id': work.id, 'title': work.title, 'state': work.processing_state} for work in works]}


def guardian_awake(batch):
    try:
        data = json.loads((ROOT/'runtime'/f'night-batch-{batch.id}.json').read_text(encoding='utf-8'))
        return data.get('sleep_prevention_active') is True and (now()-utc(datetime.fromisoformat(data['seen_at']))).total_seconds() < 90
    except (OSError, KeyError, ValueError, TypeError):
        return False
