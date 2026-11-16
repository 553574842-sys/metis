import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, or_, case
from sqlalchemy.exc import IntegrityError
from .adapters import ProcessingError
from .config import settings
from .db import Session, now, utc
from .models import Job, Attempt, Alert, Work, Creator, Scan, Report, Analysis, ServiceHeartbeat
from .pipeline import execute_job, enqueue, enqueue_scan

RETRY_DELAYS = (600, 1800, 3600)


def alert_once(db, category, message, job=None):
    query = select(Alert).where(Alert.category == category, Alert.resolved_at.is_(None))
    if job:
        query = query.where(Alert.job_id == job.id)
    if not db.scalar(query):
        db.add(Alert(category=category, message=message, severity='severe' if category != 'retry_exhausted' else 'important',
                     job_id=job.id if job else None, work_id=job.work_id if job else None,
                     creator_id=job.creator_id if job else None))


def heartbeat(db, name):
    if db.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(ServiceHeartbeat).values(name=name, seen_at=now())
    db.execute(statement.on_conflict_do_update(index_elements=['name'], set_={'seen_at': now()}))


def failure(db, job, error):
    if job.state != 'running':
        return
    if error.code == 'LEASE_LOST':
        return
    job.last_error_code, job.last_error_message = error.code, error.message
    if job.job_type == 'reflect' and getattr(error, 'diagnostic', None):
        job.payload = {**job.payload, 'diagnostic': error.diagnostic}
    job.updated_at = now()
    job.lease_expires_at = None
    if not error.retryable:
        job.state = 'blocked'
        job.next_retry_at = None
        if error.severe:
            alert_once(db, 'access_or_service_failure', error.message, job)
    elif job.attempt_count < 3:
        job.state = 'waiting'
        job.next_retry_at = now() + timedelta(seconds=max(RETRY_DELAYS[job.attempt_count], error.retry_after))
    else:
        job.state = 'exhausted'
        job.next_retry_at = now() + timedelta(hours=24)
        alert_once(db, 'retry_exhausted', '单条作业完成 3 次额外重试后仍失败；等待下一正常检查周期。', job)
    job.attempt_count += 1
    if error.code == 'AI_QUOTA_EXCEEDED':
        # One exhausted provider must not receive a separate probe for every work.
        retry_at = now() + timedelta(seconds=max(1800, error.retry_after))
        db.execute(update(Job).where(or_(Job.job_type.in_(['analyze', 'report']),
            (Job.job_type == 'reflect') & Job.payload['uses_paid_ai'].as_boolean().is_(True)),
            Job.state.in_(['queued', 'waiting']),
            or_(Job.work_id.is_(None), Job.work_id.in_(select(Work.id).where(Work.source_mode != 'mock'))),
            or_(Job.next_retry_at.is_(None), Job.next_retry_at < retry_at)).values(
                state='waiting', next_retry_at=retry_at, last_error_code='AI_QUOTA_EXCEEDED',
                last_error_message='MiniMax 额度已用完；该作业随解析队列等待，未消耗额外重试次数。'))
    if job.payload.get('batch_id'):
        from .night_batch import on_failure
        on_failure(db, job, error)
    if job.work_id and job.job_type not in {'brain_sync', 'reflect'}:
        work = db.get(Work, job.work_id)
        if job.job_type != 'cleanup' and work.processing_state != 'completed':
            work.processing_state = 'failed_retryable'
            work.access_state = error.code.lower() if not error.retryable else 'temporary_error'
        for run in db.scalars(select(Analysis).where(Analysis.work_id == job.work_id, Analysis.status == 'running')):
            run.status, run.error_code = 'failed', error.code
    if job.job_type == 'scan':
        scan = db.get(Scan, job.payload['scan_id'])
        scan.status, scan.error = ('waiting' if job.state == 'waiting' else 'failed'), error.message
        creator = db.get(Creator, job.creator_id)
        creator.access_state = error.code.lower()
        if error.retry_after:
            creator.next_check_at = max(utc(creator.next_check_at) or now(), job.next_retry_at or now())
    db.commit()


def process_job(job_id):
    fence = str(uuid4())
    with Session() as db:
        claimed = db.execute(update(Job).where(Job.id == job_id, Job.state.in_(['queued', 'waiting']),
            or_(Job.next_retry_at.is_(None), Job.next_retry_at <= now())).values(state='running', lease_token=fence,
                lease_expires_at=now() + timedelta(seconds=settings.lease_seconds), updated_at=now()))
        db.commit()
        if not claimed.rowcount:
            return False
        job = db.get(Job, job_id)
        job._fence = fence
        attempt = Attempt(job_id=job.id, attempt_no=job.attempt_count)
        db.add(attempt)
        db.commit()
        stopped = threading.Event()

        def renew_lease():
            while not stopped.wait(min(60, settings.lease_seconds / 3)):
                try:
                    with Session() as lease_db:
                        lease_db.execute(update(Job).where(Job.id == job_id, Job.state == 'running', Job.lease_token == fence).values(
                            lease_expires_at=now()+timedelta(seconds=settings.lease_seconds)))
                        heartbeat(lease_db, 'worker-capture' if job.job_type in {'scan','capture'} else 'worker-analysis')
                        lease_db.commit()
                except Exception:
                    return  # 提交前 fencing 检查将阻止失去租约的 Worker 写入。

        thread = threading.Thread(target=renew_lease, daemon=True)
        thread.start()
        error = None
        try:
            execute_job(db, job)
        except ProcessingError as exc:
            error = exc
        except Exception as exc:
            # 外部异常正文可能包含凭据或路径；只持久化异常类型。
            error = ProcessingError('PROCESSING_ERROR', f'处理失败（{type(exc).__name__}），请检查作业与配置。')
        finally:
            stopped.set()
            thread.join(timeout=2)
        if error:
            db.rollback()
            db.refresh(job)
            if job.lease_token == fence:
                failure(db, job, error)
        db.refresh(attempt)
        attempt.ended_at = now()
        if error:
            attempt.error_category, attempt.error_message_redacted = error.code, error.message
        db.commit()
        return True


def recover(db):
    for job in db.scalars(select(Job).where(Job.state == 'running', Job.lease_expires_at < now())):
        job.state, job.lease_token = 'queued', None
        job.next_retry_at = now()
        job.updated_at = now()
        if job.work_id and job.job_type not in {'brain_sync', 'reflect'}:
            work = db.get(Work, job.work_id)
            if work.processing_state in {'capturing', 'analyzing'}:
                work.processing_state = 'pending_analysis' if job.job_type == 'analyze' else 'pending_capture'
            for run in db.scalars(select(Analysis).where(Analysis.work_id == work.id, Analysis.status == 'running')):
                run.status, run.error_code = 'interrupted', 'WORKER_INTERRUPTED'
    db.commit()


def schedule(db):
    recover(db)
    for creator in db.scalars(select(Creator).where(Creator.tracking_state == 'tracking', Creator.next_check_at <= now())):
        if db.scalar(select(Job.id).where(Job.creator_id==creator.id, Job.job_type=='scan', Job.state=='blocked')):
            continue
        expected = creator.next_check_at
        changed = db.execute(update(Creator).where(Creator.id == creator.id, Creator.next_check_at == expected,
                            Creator.tracking_state == 'tracking').values(next_check_at=now()+timedelta(hours=24)))
        if changed.rowcount:
            enqueue_scan(db, creator)
    for job in db.scalars(select(Job).where(Job.state == 'exhausted', Job.next_retry_at <= now())):
        creator = db.get(Creator, job.creator_id) if job.creator_id else None
        if creator and creator.tracking_state != 'tracking':
            continue
        job.state, job.attempt_count = 'queued', 0
        job.next_retry_at = now()
    local = now().astimezone(ZoneInfo(settings.app_timezone))
    target = local.date() if (local.hour, local.minute) >= (settings.report_hour, settings.report_minute) else local.date()-timedelta(days=1)
    latest = db.scalar(select(Report.report_date).order_by(Report.report_date.desc()).limit(1))
    earliest = db.scalar(select(Creator.created_at).order_by(Creator.created_at).limit(1))
    first = datetime.fromisoformat(latest).date()+timedelta(days=1) if latest else utc(earliest).astimezone(ZoneInfo(settings.app_timezone)).date() if earliest else target
    for _ in range(30):
        if first > target:
            break
        date_string = first.isoformat()
        enqueue(db, f'report:{settings.app_timezone}:{date_string}', 'report', payload={'date': date_string})
        first += timedelta(days=1)
    heartbeat(db, 'scheduler')
    db.commit()


def due_jobs(db, types=None, limit=50, exclude_types=None):
    query = select(Job.id).where(Job.state.in_(['queued', 'waiting']), or_(Job.next_retry_at.is_(None), Job.next_retry_at <= now()))
    if types:
        query = query.where(Job.job_type.in_(types))
    if exclude_types:
        query = query.where(Job.job_type.not_in(exclude_types))
    candidates = list(db.scalars(query.order_by(case((Job.job_type == 'brain_sync', 1), else_=0), Job.created_at).limit(limit)))
    from .night_batch import defer_brain
    return [identity for identity in candidates if db.get(Job, identity).job_type != 'brain_sync'
            or not defer_brain(db, db.get(Job, identity))]


def run_until_idle(max_jobs=5000):
    count = 0
    while count < max_jobs:
        with Session() as db:
            ids = due_jobs(db)
        if not ids:
            break
        for job_id in ids:
            process_job(job_id)
            count += 1
    return count


def local_background_supervisor(stop, kind):
    # One worker per background stage; a long brain write cannot block reflection.
    while not stop.is_set():
        try:
            with Session() as db:
                ids = due_jobs(db, types=[kind], limit=1)
            for job_id in ids:
                if not stop.is_set():
                    process_job(job_id)
        except Exception:
            (settings.data_dir / 'service-failure.flag').write_text('Background worker unavailable', encoding='utf-8')
        stop.wait(1)


def local_supervisor(stop):
    for kind in ['brain_sync', 'reflect']:
        threading.Thread(target=local_background_supervisor, args=(stop, kind), daemon=True).start()
    last_schedule = 0
    while not stop.is_set():
        try:
            with Session() as db:
                if time.monotonic() - last_schedule >= 30:
                    schedule(db)
                    last_schedule = time.monotonic()
                heartbeat(db, 'worker-capture')
                heartbeat(db, 'worker-analysis')
                fault = settings.data_dir / 'service-failure.flag'
                if fault.exists():
                    alert_once(db, 'database_or_worker_failure', '后台服务曾中断，数据库恢复后记录此异常。')
                    fault.unlink()
                db.commit()
                ids = due_jobs(db, limit=10, exclude_types=['brain_sync', 'reflect'])
            for job_id in ids:
                if stop.is_set():
                    break
                process_job(job_id)
        except Exception:
            (settings.data_dir / 'service-failure.flag').write_text('Service unavailable', encoding='utf-8')
        stop.wait(1)
