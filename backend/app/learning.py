"""Read-only learning dashboard: live database state plus finite study records."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import load_only

from .config import ROOT, settings
from .db import now, utc
from .models import Analysis, Creator, Job, Knowledge, Work

RUNTIME_ROOT = ROOT / 'runtime'
ACTIVE = {'queued', 'running', 'waiting'}
PHASES = {'video_transcription_and_filtering': '转写与筛选', 'visual_review': '核对视频画面',
          'analysis_and_brain_sync': '解析与神经元同步', 'verification': '核验学习结果', 'completed': '学习完成'}


def read_record(path):
    try:
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def original_url(item):
    identity = str(item.get('platform_work_id', ''))
    if re.fullmatch(r'\d{10,25}', identity):
        return 'https://www.douyin.com/' + ('note/' if item.get('content_type') == 'image' else 'video/') + identity
    return ''


def status(db, run_id='', creator_id='', q='', offset=0, limit=12):
    studies = sorted((p for p in RUNTIME_ROOT.glob('logo-topic-*') if p.is_dir() and not p.is_symlink()
                      and (p / 'study.json').is_file()), key=lambda p: p.name, reverse=True)
    runs = [{'id': p.name, 'title': (read_record(p / 'study.json') or {}).get('topic', '专题学习')} for p in studies]
    chosen = run_id or (runs[0]['id'] if runs else 'all')
    if chosen != 'all' and chosen not in {r['id'] for r in runs}:
        raise HTTPException(404, '学习任务不存在')
    directory = next((p for p in studies if p.name == chosen), None)
    study = read_record(directory / 'study.json') if directory else {}
    snapshot = read_record(directory / 'progress.json') if directory else {}
    study, snapshot = study or {}, snapshot or {}
    metadata, people = {}, {}
    if directory:
        for path in directory.glob('MS4*.json'):
            inventory = read_record(path) or {}
            identity = inventory.get('identity')
            if not isinstance(identity, str) or not isinstance(inventory.get('works'), dict):
                continue
            people[identity] = {'id': identity, 'name': inventory.get('creator', '未命名博主'),
                'profile_url': 'https://www.douyin.com/user/' + identity, 'inventoried': 0, 'videos': 0, 'images': 0}
            for work_id, item in inventory['works'].items():
                if not isinstance(item, dict) or not re.fullmatch(r'\d{10,25}', work_id):
                    continue
                metadata[work_id] = {**item, 'creator_key': identity}
                people[identity]['inventoried'] += 1
                people[identity]['videos' if item.get('content_type') == 'video' else 'images'] += 1
    entries = study.get('items', {}) if isinstance(study.get('items'), dict) else {}
    scoped_ids = [x['work_id'] for x in entries.values() if isinstance(x, dict) and isinstance(x.get('work_id'), str)]
    light_work = load_only(Work.id, Work.creator_id, Work.platform_work_id, Work.title, Work.content_type,
                          Work.platform, Work.processing_state, Work.source_mode, Work.current_analysis_id,
                          Work.original_url, Work.discovered_at)
    light_creator = load_only(Creator.id, Creator.platform_creator_id, Creator.display_name, Creator.profile_url)
    light_analysis = load_only(Analysis.id, Analysis.status, Analysis.provider)
    query = select(Work, Creator, Analysis).options(light_work, light_creator, light_analysis).join(Creator, Creator.id == Work.creator_id).outerjoin(Analysis, Analysis.id == Work.current_analysis_id)
    query = query.where(Work.id.in_(scoped_ids)) if directory else query.where(Work.source_mode != 'mock')
    rows = list(db.execute(query.order_by(Work.discovered_at.desc())))
    work_ids = [work.id for work, _, _ in rows]
    jobs = list(db.scalars(select(Job).where(Job.work_id.in_(work_ids)).order_by(Job.updated_at.desc())))
    knowledge_rows = list(db.execute(select(Knowledge, Work, Creator, Analysis)
        .options(light_work, light_creator, light_analysis,
                 load_only(Knowledge.id, Knowledge.work_id, Knowledge.analysis_id, Knowledge.item_type, Knowledge.title,
                           Knowledge.content_zh, Knowledge.content_original, Knowledge.claim_kind, Knowledge.created_at))
        .join(Work, Work.id == Knowledge.work_id).join(Creator, Creator.id == Work.creator_id)
        .join(Analysis, Analysis.id == Work.current_analysis_id)
        .where(Work.id.in_(work_ids), Knowledge.analysis_id == Work.current_analysis_id, Analysis.status == 'success')
        .order_by(Knowledge.created_at.desc(), Knowledge.id)))
    knowledge_counts = Counter(item.work_id for item, _, _, _ in knowledge_rows)
    jobs_by_work = defaultdict(list)
    for job in jobs:
        jobs_by_work[job.work_id].append(job)
    completed = {w.id for w, _, a in rows if w.processing_state == 'completed' and a and a.status == 'success'}
    current_analysis = {w.id: w.current_analysis_id for w, _, _ in rows}
    brain_jobs = {j.work_id: j for j in jobs if j.job_type == 'brain_sync' and j.payload.get('analysis_id') == current_analysis.get(j.work_id)}
    activities, works = [], []
    for work, creator, analysis in rows:
        creator_key = creator.platform_creator_id or creator.id
        if creator_key not in people:
            people[creator_key] = {'id': creator_key, 'name': creator.display_name, 'profile_url': creator.profile_url,
                                   'inventoried': 0, 'videos': 0, 'images': 0}
        person = people[creator_key]
        person['selected'] = person.get('selected', 0) + 1
        person['completed'] = person.get('completed', 0) + int(work.id in completed)
        person['knowledge_count'] = person.get('knowledge_count', 0) + knowledge_counts[work.id]
        work_jobs = jobs_by_work[work.id]
        current = next((j for j in work_jobs if j.state == 'running'), None)
        waiting = next((j for j in work_jobs if j.state == 'waiting'), None)
        blocked = next((j for j in work_jobs if j.state in {'blocked', 'exhausted'} and
                        (work.id not in completed or j.job_type in {'brain_sync', 'reflect'} and j.payload.get('analysis_id') == work.current_analysis_id)), None)
        source_url = original_url({'platform_work_id': work.platform_work_id, 'content_type': work.content_type}) if work.platform == 'douyin' else work.original_url
        sync = brain_jobs.get(work.id)
        value = {'id': work.id, 'platform_work_id': work.platform_work_id, 'creator_id': creator_key, 'creator': creator.display_name,
                 'title': work.title, 'content_type': work.content_type, 'source_url': source_url,
                 'state': work.processing_state, 'knowledge_count': knowledge_counts[work.id],
                 'brain_state': sync.state if sync else 'not_started',
                 'next_retry_at': (waiting.next_retry_at.isoformat() if waiting and waiting.next_retry_at else None),
                 'error': (blocked or waiting).last_error_message if blocked or waiting else None}
        works.append(value)
        if current:
            phase = {'capture': '采集原始素材', 'brain_sync': '同步神经元知识', 'reflect': '深入学习与延伸', 'cleanup': '整理素材'}.get(current.job_type)
            if current.job_type == 'analyze':
                context = work.media_context or {}
                phase = '提取知识与建立索引' if 'ocr_segments' in context else '理解图片与视频画面' if 'transcript_segments' in context or work.content_type == 'image' else '转写视频音轨'
            activities.append({**value, 'stage': phase or '处理作品', 'live': True, 'observed_at': utc(current.updated_at).isoformat(),
                               'job_id': current.id, 'sync_documents': len(current.payload.get('documents', {}))})
    pending = []
    for work_id, entry in entries.items():
        if not isinstance(entry, dict) or entry.get('decision') != 'uncertain' or work_id not in metadata:
            continue
        item = metadata[work_id]
        person = people.get(item['creator_key'], {})
        pending.append({'platform_work_id': work_id, 'title': item.get('title', ''), 'creator_id': item['creator_key'],
                        'creator': person.get('name', ''), 'content_type': item.get('content_type', 'video'), 'source_url': original_url(item)})
    worker_live = False
    if directory:
        activity = read_record(directory / 'worker-activity.json') or {}
        try:
            heartbeat = datetime.fromisoformat(activity.get('heartbeat_at', ''))
            worker_live = activity.get('status') == 'running' and 0 <= (now() - utc(heartbeat)).total_seconds() < 45
        except (TypeError, ValueError):
            worker_live = False
        identity = activity.get('platform_work_id')
        if worker_live and identity in metadata and activity.get('stage') in {'核对视频画面', '转写与筛选'}:
            item = metadata[identity]
            activities.insert(0, {'id': None, 'platform_work_id': identity, 'title': item.get('title', ''),
                'creator_id': item['creator_key'], 'creator': people[item['creator_key']]['name'],
                'content_type': item.get('content_type', 'video'), 'source_url': original_url(item),
                'stage': activity['stage'], 'live': True, 'observed_at': activity['heartbeat_at'], 'job_id': None})
        else:
            worker_live = False
    # Older running collectors have no per-step heartbeat. File observations are
    # explicitly labelled recent activity, never represented as a live signal.
    if directory and not worker_live and snapshot.get('stage') == 'visual_review' and pending:
        frames = []
        for item in pending:
            frame_dir = settings.data_dir / 'topic_review' / ('tech-logo-' + directory.name.removeprefix('logo-topic-')) / item['platform_work_id']
            for path in frame_dir.glob('review-frame-*.jpg'):
                frames.append((path.stat().st_mtime, item))
        if frames:
            stamp, item = max(frames, key=lambda x: x[0])
            activities.insert(0, {**item, 'id': None, 'stage': '最近核对视频画面', 'live': False,
                                  'observed_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(), 'job_id': None})
    videos_total = sum(x.get('content_type') == 'video' for x in metadata.values())
    reviewed = sum(isinstance(v, dict) and v.get('content_type') == 'video' and v.get('decision') in {'confirmed', 'unrelated', 'uncertain'} for v in entries.values())
    for person in people.values():
        person.setdefault('selected', 0)
        person.setdefault('completed', 0)
        person.setdefault('knowledge_count', 0)
    visible_knowledge = [(k, w, c, a) for k, w, c, a in knowledge_rows
                         if (not creator_id or creator_id == (c.platform_creator_id or c.id))
                         and (not q or q.casefold() in (k.title + '\n' + k.content_zh).casefold())]
    items = [{'knowledge_item_id': k.id, 'work_id': w.id, 'creator_id': c.platform_creator_id or c.id,
              'creator': c.display_name, 'platform': w.platform, 'title': k.title, 'summary': k.content_zh,
              'source_url': original_url({'platform_work_id': w.platform_work_id, 'content_type': w.content_type}) if w.platform == 'douyin' else w.original_url,
              'evidence_excerpt': k.content_original, 'relevance': 1, 'source_mode': w.source_mode,
              'analysis_mode': a.provider, 'claim_kind': k.claim_kind, 'item_type': k.item_type,
              'learned_at': utc(k.created_at).isoformat(), 'work_title': w.title}
             for k, w, c, a in visible_knowledge[offset:offset + limit]]
    synced = sum(j.state == 'completed' for j in brain_jobs.values())
    phase = snapshot.get('stage', '')
    phase = phase if isinstance(phase, str) else ''
    if worker_live and phase.startswith('needs_attention'):
        phase = 'visual_review'
    if phase == 'completed' and (len(completed) != len(rows) or synced != len(completed) or pending):
        phase = 'visual_review' if pending else 'analysis_and_brain_sync'
    failed = sum(w.processing_state == 'failed_retryable' for w, _, _ in rows)
    return {'generated_at': now().isoformat(), 'refresh_seconds': 5,
        'run': {'id': chosen, 'title': study.get('topic', '全部学习任务'), 'phase': phase,
                'phase_label': '需要查看任务' if phase.startswith('needs_attention') else PHASES.get(phase, '学习进行中' if activities else '已收录的学习任务'),
                'snapshot_at': snapshot.get('checked_at'), 'has_inventory': bool(metadata),
                'record_warning': '专题运行记录暂不可读，请稍后刷新。' if directory and not study else None},
        'runs': runs + [{'id': 'all', 'title': '全部学习任务'}], 'creators': list(people.values()),
        'counts': {'inventoried': len(metadata), 'reviewed': reviewed, 'review_total': videos_total,
                   'pending_review': len(pending), 'excluded': sum(isinstance(v, dict) and v.get('decision') == 'unrelated' for v in entries.values()),
                   'selected': len(rows), 'completed': len(completed), 'failed': failed, 'knowledge': len(knowledge_rows),
                   'brain_completed': synced, 'brain_total': len(completed)},
        'activities': activities, 'works': works, 'pending_review': pending,
        'knowledge': {'items': items, 'total': len(visible_knowledge), 'offset': offset, 'limit': limit}}
