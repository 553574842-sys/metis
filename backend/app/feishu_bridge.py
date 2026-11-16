"""Reuse the existing inbox and work queue for a single SSH-paired OpenClaw."""
import json
import re
import shlex
import subprocess
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from .adapters import ProcessingError, parse_profile
from .config import settings
from .db import now
from .inbox import work_record
from .models import Audit, Creator, InboxSubmission, Job, Key, Knowledge, Work
from .pipeline import enqueue_link_capture, enqueue_scan
from .security import digest


class BridgeError(Exception):
    pass


def exchange(config, updates, service_ready):
    host, script = config['ssh_host'], config['remote_script']
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', host)
            or not re.fullmatch(r'/[A-Za-z0-9_./-]+\.py', script)):
        raise BridgeError('BRIDGE_CONFIG_INVALID')
    command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=10', host, 'python3 ' + shlex.quote(script) + ' rpc']
    payload = {'worker_id': config['worker_id'], 'updates': updates, 'service_ready': service_ready,
               'accept_creator_requests': True}
    try:
        result = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                text=True, encoding='utf-8', timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or len(result.stdout) > 2_000_000:
            raise BridgeError('SSH_EXCHANGE_FAILED')
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or not isinstance(response.get('items'), list) or len(response['items']) > 100:
            raise BridgeError('BRIDGE_RESPONSE_INVALID')
        return response['items']
    except (OSError, subprocess.SubprocessError, ValueError):
        raise BridgeError('SSH_EXCHANGE_FAILED') from None


def integration_key(db, worker_id):
    marker = digest('creator-atlas-openclaw:' + worker_id)
    key = db.scalar(select(Key).where(Key.kind == 'integration', Key.key_hash == marker))
    if not key:
        # This kind is rejected by every HTTP authentication path. No login token is issued.
        key = Key(name='飞书 · OpenClaw', key_hash=marker, kind='integration', scope='inbox')
        db.add(key)
        db.commit()
    if key.revoked_at:
        raise BridgeError('BRIDGE_REVOKED')
    return key


def snapshot(db, submission, work):
    creator = db.get(Creator, work.creator_id)
    result = work_record(db, submission, work, creator)
    knowledge = list(db.scalars(select(Knowledge).where(Knowledge.work_id == work.id,
                          Knowledge.analysis_id == work.current_analysis_id).order_by(Knowledge.created_at))) if result['knowledge_done'] else []
    result['knowledge'] = [{'title': item.title[:300], 'summary': item.content_zh[:900],
                            'claim_kind': item.claim_kind} for item in knowledge[:12]]
    result['knowledge_truncated'] = len(knowledge) > 12
    return result


def register_creator(db, key, item):
    url = item.get('source_url', '')
    if not isinstance(url, str) or not re.fullmatch(r'https://www\.douyin\.com/user/[A-Za-z0-9_-]{8,200}', url):
        raise BridgeError('CREATOR_PROFILE_INVALID')
    receipt = db.scalar(select(Audit).where(Audit.actor == 'integration:' + key.id,
                        Audit.action == 'douyin_folder_creator_received', Audit.target_id == item['id']))
    if receipt:
        creator = db.get(Creator, receipt.details_redacted['creator_id'])
        if (not creator or creator.platform != 'douyin' or creator.platform_creator_id != url.rsplit('/', 1)[-1]
                or item.get('creator_id') not in {None, creator.id}):
            raise BridgeError('LOCAL_CREATOR_MAPPING_INVALID')
    else:
        if item.get('creator_id'):
            raise BridgeError('LOCAL_CREATOR_MAPPING_INVALID')
        if not settings.platform_requests_enabled or settings.adapter_douyin_mode != 'browser':
            raise ProcessingError('ACCESS_NOT_CONFIGURED', '本机账号采集尚未启用。', False)
        parsed = parse_profile(url, 'douyin')
        creator = db.scalar(select(Creator).where(Creator.platform == 'douyin',
                             Creator.platform_creator_id == parsed['platform_creator_id']))
        if creator and creator.source_mode != 'real':
            raise BridgeError('CREATOR_SOURCE_INVALID')
        if creator and db.scalar(select(Job.id).where(Job.creator_id == creator.id,
                                  Job.job_type == 'scan', Job.state == 'blocked')):
            raise ProcessingError('CREATOR_SCAN_BLOCKED', '账号扫描已暂停，需要先处理现有采集错误。', False)
        name = item.get('progress', {}).get('creator_name', '')
        if not isinstance(name, str) or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise BridgeError('CREATOR_NAME_INVALID')
        if not creator:
            creator = Creator(**parsed, profile_url=url, display_name=name or parsed['platform_creator_id'][:18],
                              source_mode='real')
            db.add(creator)
            db.flush()
        creator.tracking_state, creator.next_check_at = 'tracking', now() + timedelta(hours=24)
        for job in db.scalars(select(Job).where(Job.creator_id == creator.id, Job.job_type == 'capture', Job.state == 'canceled')):
            job.state, job.next_retry_at = 'queued', now()
        enqueue_scan(db, creator, 'history')
        db.add(Audit(actor='integration:' + key.id, action='douyin_folder_creator_received',
                     target_type='creator_request', target_id=item['id'], details_redacted={'creator_id': creator.id}))
        db.commit()  # Scan and receipt commit together; lost cloud acknowledgements cannot restart a scan.
    return creator.id, {'state': 'tracking_registered', 'stage': '已登记账号，后台扫描历史作品',
                        'creator_name': creator.display_name, 'knowledge_done': False, 'brain_state': 'not_started',
                        'error': None}


def process_items(db, worker_id, items, service_ready=True):
    if not service_ready:
        return []
    key = integration_key(db, worker_id)
    updates = []
    for item in items:
        identity, work_id = item.get('id', ''), item.get('work_id')
        creator_id = item.get('creator_id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise BridgeError('BRIDGE_RESPONSE_INVALID')
        try:
            if item.get('request_kind', 'work') == 'creator':
                if work_id is not None:
                    raise BridgeError('BRIDGE_RESPONSE_INVALID')
                creator_id, progress = register_creator(db, key, item)
                updates.append({'id': identity, 'work_id': None, 'creator_id': creator_id,
                                'progress': progress, 'delivery_id': uuid4().hex})
                continue
            if item.get('request_kind', 'work') != 'work' or creator_id is not None:
                raise BridgeError('BRIDGE_RESPONSE_INVALID')
            new_item = not work_id
            if not work_id:
                result = enqueue_link_capture(db, item.get('source_url', ''))
                work_id = result['work_id']
            work = db.get(Work, work_id)
            if not work or work.platform != 'douyin':
                raise BridgeError('LOCAL_WORK_MISSING')
            submission = db.scalar(select(InboxSubmission).where(InboxSubmission.device_id == key.id,
                                                                 InboxSubmission.work_id == work.id))
            if not new_item and not submission:
                raise BridgeError('LOCAL_MAPPING_INVALID')
            if not submission:
                submission = InboxSubmission(device_id=key.id, work_id=work.id)
                db.add(submission)
                db.add(Audit(actor='integration:' + key.id, action='feishu_link_received', target_type='work',
                             target_id=work.id, details_redacted={'remote_submission_id': identity}))
                db.commit()
            progress = snapshot(db, submission, work)
        except ProcessingError as exc:
            db.rollback()
            progress = {'state': 'submission_failed', 'stage': '链接提交失败', 'knowledge_done': False,
                        'error': exc.message, 'error_code': exc.code, 'brain_state': 'not_started',
                        'retryable': exc.retryable, 'retry_after_seconds': exc.retry_after}
        except BridgeError as exc:
            db.rollback()
            progress = {'state': 'submission_failed', 'stage': '需要检查本机记录', 'knowledge_done': False,
                        'error_code': str(exc), 'brain_state': 'not_started', 'retryable': False}
        updates.append({'id': identity, 'work_id': work_id,
                        **({'creator_id': creator_id} if item.get('request_kind') == 'creator' else {}),
                        'progress': progress, 'delivery_id': uuid4().hex})
    return updates
