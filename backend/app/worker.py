from celery import Celery
from celery.signals import worker_ready
from .config import settings
from .db import Session
from .jobs import schedule, due_jobs, process_job, heartbeat, alert_once

celery_app = Celery('creator_atlas', broker=settings.redis_url)
celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
                      broker_connection_retry_on_startup=True, timezone='UTC',
                      task_routes={'app.worker.capture_job': {'queue': 'capture'}, 'app.worker.analysis_job': {'queue': 'analysis'},
                                   'app.worker.tick': {'queue': 'capture'}},
                      beat_schedule={'persistent-due-dispatch': {'task': 'app.worker.tick', 'schedule': 30.0}},
                      broker_transport_options={'visibility_timeout': 3600})


@celery_app.task(name='app.worker.capture_job')
def capture_job(job_id):
    return process_job(job_id)


@celery_app.task(name='app.worker.analysis_job')
def analysis_job(job_id):
    return process_job(job_id)


@celery_app.task(name='app.worker.analysis_heartbeat', queue='analysis')
def analysis_heartbeat():
    with Session() as db:
        heartbeat(db, 'worker-analysis')
        db.commit()


@celery_app.task(name='app.worker.tick')
def tick():
    with Session() as db:
        schedule(db)
        heartbeat(db, 'worker-capture')
        for name in ['worker-analysis']:
            from .models import ServiceHeartbeat
            from .db import now, utc
            row = db.get(ServiceHeartbeat, name)
            if row and (now()-utc(row.seen_at)).total_seconds() > 300:
                alert_once(db, 'worker_unavailable', '解析 Worker 超过 5 分钟未响应。')
        db.commit()
        ids = due_jobs(db)
        from .models import Job
        kinds = {job_id: db.get(Job, job_id).job_type for job_id in ids}
    for job_id in ids:
        (capture_job if kinds[job_id] in {'scan', 'capture'} else analysis_job).delay(job_id)
    analysis_heartbeat.delay()


@worker_ready.connect
def ready(**kwargs):
    tick.delay()
