"""Versioned, evidence-preserving exports to the user's local Personal Brain."""
import hashlib
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, func

from .adapters import ProcessingError
from .config import settings
from .db import now
from .models import Analysis, Creator, Job, Knowledge, Work

DATASET = 'creator_atlas'


def brain_url():
    parts = urlsplit(settings.brain_base_url)
    if (parts.scheme != 'http' or parts.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parts.username or parts.password or parts.path not in {'', '/'} or parts.query or parts.fragment):
        raise ProcessingError('BRAIN_URL_INVALID', 'Personal Brain 仅允许本机 HTTP 服务地址。', False)
    return settings.brain_base_url.rstrip('/')


def eligible():
    return (select(Work).join(Analysis, Analysis.id == Work.current_analysis_id)
            .where(Work.processing_state == 'completed', Work.source_mode.in_(['manual', 'real']),
                   Analysis.status == 'success', Analysis.provider != 'mock',
                   select(Knowledge.id).where(Knowledge.work_id == Work.id,
                                             Knowledge.analysis_id == Work.current_analysis_id).exists()))


def document(item, work, creator, analysis):
    # Keep these values verbatim. Cognee receives a copy, not permission to
    # rewrite source records or convert an inference into an observed fact.
    text = '\n'.join([
        'Creator Atlas 知识快照 v2',
        f'knowledge_item_id: {item.id}', f'work_id: {work.id}', f'analysis_id: {analysis.id}',
        f'来源类型: {work.source_mode}', f'出处记录名称: {creator.display_name}',
        f'作品标题: {work.title}', f'原作品链接: {work.original_url}',
        f'作品发布时间: {work.published_at.isoformat() if work.published_at else "未提供"}',
        f'解析完成时间: {analysis.completed_at.isoformat() if analysis.completed_at else "未提供"}',
        f'结论属性: {item.claim_kind}（inference 表示 AI 推论，source_claim 表示原文观点）',
        '本资料为解析版本快照；可能存在更新版本，以 Creator Atlas 当前记录为准。',
        '以下内容与引文均保留原字句；作者观点及 AI 推论不自动代表用户认同。',
        '知识标题:', item.title, '知识内容:', item.content_zh,
        '原文引文:', item.content_original,
        'source_segment_ids: ' + ','.join(item.source_segment_ids),
    ])
    if len(text) > 16000:
        raise ProcessingError('BRAIN_DOCUMENT_TOO_LARGE', '单条知识超过 Personal Brain 导入上限，未截断原文。', False)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    label = f'creator-atlas:{work.id}:{analysis.id}:{item.id}:{digest}'
    return {'text': text, 'dataset': DATASET, 'source_name': label, 'chunk_size': 512}


def compatible_document(payload, previous):
    if not previous or previous.get('source_name') == payload['source_name']:
        return payload
    # v1 differed only in its header and attribution legend. Reconstruct it
    # solely when its entire export hash proves the saved content is identical.
    lines = payload['text'].split('\n')
    lines[0] = 'Creator Atlas 知识快照 v1'
    index = next(i for i, line in enumerate(lines) if line.startswith('结论属性: '))
    lines[index] = lines[index].replace('source_claim 表示原文观点', 'original 表示原文观点', 1)
    text = '\n'.join(lines)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    label = payload['source_name'].rsplit(':', 1)[0] + ':' + digest
    if previous.get('source_name') != label or previous.get('text_sha256') != digest:
        raise ProcessingError('BRAIN_SOURCE_CHANGED', '已同步版本的内容发生变化，旧快照保留。', False)
    return {**payload, 'text': text, 'source_name': label}


def enqueue_work(db, work):
    from .pipeline import enqueue
    if not settings.brain_sync_enabled or not db.scalar(eligible().where(Work.id == work.id)):
        return None
    return enqueue(db, f'brain:{work.id}:{work.current_analysis_id}', 'brain_sync', work.creator_id, work.id,
                   {'analysis_id': work.current_analysis_id, 'dataset': DATASET, 'documents': {}})


def enqueue_all(db):
    if not settings.brain_sync_enabled:
        raise ProcessingError('BRAIN_SYNC_DISABLED', 'Personal Brain 同步尚未启用。', False)
    brain_url()
    jobs = [enqueue_work(db, work) for work in db.scalars(eligible())]
    for job in jobs:
        if job.state in {'blocked', 'exhausted'}:
            job.state, job.next_retry_at, job.attempt_count = 'queued', now(), 0
            job.last_error_code, job.last_error_message = None, None
    db.commit()
    return {'jobs': [{'id': job.id, 'state': job.state} for job in jobs], 'dataset': DATASET}


def status(db):
    works = eligible().subquery()
    count = db.scalar(select(func.count()).select_from(Knowledge).join(works, Knowledge.work_id == works.c.id)
                      .where(Knowledge.analysis_id == works.c.current_analysis_id))
    jobs = list(db.scalars(select(Job).where(Job.job_type == 'brain_sync').order_by(Job.created_at.desc()).limit(20)))
    first_document = next((doc['document_id'] for job in jobs for doc in job.payload.get('documents', {}).values()), '')
    panel_url = brain_url() + '/panel/?dataset=' + DATASET
    if first_document:
        from urllib.parse import urlencode
        panel_url += '&' + urlencode({'document': first_document})
    return {'enabled': settings.brain_sync_enabled, 'panel_url': panel_url, 'dataset': DATASET,
            'eligible_knowledge_count': count,
            'jobs': [{'id': job.id, 'work_id': job.work_id, 'state': job.state,
                      'synced_documents': len(job.payload.get('documents', {})),
                      'last_error_message': job.last_error_message} for job in jobs]}


def request(client, method, path, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise ProcessingError('BRAIN_UNAVAILABLE', 'Personal Brain 连接中断；下次重试会先核对已有导入。') from None
    if not response.is_success:
        raise ProcessingError('BRAIN_REQUEST_FAILED', 'Personal Brain 请求未完成；原始响应未写入日志。',
                              response.status_code >= 500 or response.status_code == 429)
    try:
        return response.json()
    except ValueError:
        raise ProcessingError('BRAIN_RESPONSE_INVALID', 'Personal Brain 返回格式异常。', False) from None


def verify_document(client, label, text):
    documents = request(client, 'GET', '/documents', params={'dataset': DATASET})
    if not isinstance(documents, list):
        raise ProcessingError('BRAIN_RESPONSE_INVALID', 'Personal Brain 文档清单格式异常。', False)
    matches = [doc for doc in documents if isinstance(doc, dict) and doc.get('name') == label]
    if not matches:
        return None
    if len(matches) != 1:
        raise ProcessingError('BRAIN_IDENTITY_AMBIGUOUS', '同步文档身份不唯一，已停止自动导入。', False)
    remote = matches[0]
    graph = request(client, 'GET', '/graph')  # Also waits for the target ingestion lock.
    chunks = [ref for node in graph.get('nodes', []) for ref in node.get('refs', [])
              if ref.get('engine') == 'cognee' and ref.get('properties', {}).get('type') == 'DocumentChunk'
              and str(ref['properties'].get('document_id')) == remote.get('document_id')]
    if not chunks:
        raise ProcessingError('BRAIN_IMPORT_UNCONFIRMED', '目标已有资料但未确认生成知识片段，已暂停以避免重复导入。', False)
    source_texts = []
    for chunk in sorted(chunks, key=lambda ref: ref['properties'].get('chunk_index', 0)):
        proof = request(client, 'GET', '/source/cognee/' + chunk['engine_id'])
        if (not proof.get('hash_verified') or not proof.get('source_resolved')
                or proof.get('source', {}).get('document_id') != remote.get('document_id')
                or proof.get('text') not in text):
            raise ProcessingError('BRAIN_EVIDENCE_INVALID', '目标知识片段的来源或哈希未通过核对。', False)
        source_texts.append(proof['text'])
    # Source snippets may omit whitespace between tokenizer chunks. Each
    # snippet is checked above; the exact export hash is retained separately.
    return {'document_id': remote['document_id'], 'source_name': label,
            'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'chunk_ids': [chunk['engine_id'] for chunk in chunks], 'verified_at': now().isoformat()}


def sync(db, job):
    from .pipeline import assert_owner
    if job.payload.get('reflection_id'):
        return sync_reflection(db, job)
    if not settings.brain_sync_enabled:
        raise ProcessingError('BRAIN_SYNC_DISABLED', 'Personal Brain 同步尚未启用。', False)
    work = db.get(Work, job.work_id)
    analysis = db.get(Analysis, job.payload.get('analysis_id'))
    if not analysis or analysis.id != work.current_analysis_id or not db.scalar(eligible().where(Work.id == work.id)):
        assert_owner(db, job)
        job.state = 'canceled'
        db.commit()
        return
    creator = db.get(Creator, work.creator_id)
    items = list(db.scalars(select(Knowledge).where(Knowledge.analysis_id == analysis.id).order_by(Knowledge.id)))
    # ponytail: reuse the durable single-worker queue; a separate ingestion
    # worker is needed if local model latency starts delaying other jobs.
    with httpx.Client(base_url=brain_url(), trust_env=False, timeout=900, follow_redirects=False) as client:
        for item in items:
            if job.payload.get('batch_id'):
                from .night_batch import before_job
                if not before_job(db, job):
                    return
            payload = document(item, work, creator, analysis)
            previous = job.payload.get('documents', {}).get(item.id)
            payload = compatible_document(payload, previous)
            try:
                verified = verify_document(client, payload['source_name'], payload['text'])
            except ProcessingError as error:
                if error.code != 'BRAIN_IMPORT_UNCONFIRMED' or not job.payload.get('resume_unconfirmed'):
                    raise
                # Explicit recovery of this exact version only. Cognee's add
                # deduplicates the identical source; verification still follows.
                verified = None
            if not verified:
                request(client, 'POST', '/ingest/knowledge', json=payload)
                verified = verify_document(client, payload['source_name'], payload['text'])
            if not verified:
                raise ProcessingError('BRAIN_IMPORT_UNCONFIRMED', '导入后未找到唯一的来源文档。', False)
            assert_owner(db, job)
            job.payload = {**job.payload, 'documents': {**job.payload.get('documents', {}), item.id: verified}}
            db.commit()  # Each verified source survives interruption independently.
    assert_owner(db, job)
    job.state, job.last_error_code, job.last_error_message = 'completed', None, None
    db.commit()


def sync_reflection(db, job):
    from .models import LearningReflection
    from .pipeline import assert_owner
    from .reflections import render_output
    if not settings.brain_sync_enabled:
        raise ProcessingError('BRAIN_SYNC_DISABLED', 'Personal Brain 同步尚未启用。', False)
    row = db.get(LearningReflection, job.payload['reflection_id'])
    work = db.get(Work, row.work_id)
    if row.status != 'completed' or work.current_analysis_id != row.analysis_id:
        assert_owner(db, job)
        job.state = 'canceled'
        db.commit()
        return
    text = '\n'.join(['Creator Atlas 深入学习 v1', '结论属性: inference（全部为 AI 推论，尚未外网核实）',
                      '这些推论不代表原作者观点，也不代表用户已经采纳。',
                      f'reflection_id: {row.id}', f'work_id: {work.id}', f'model: {row.model}',
                      '原作品: ' + next(source['source_url'] for source in row.sources if source['work_id'] == row.work_id), render_output(row.output),
                      '生成时采用的用户自述背景（不推定未提供的个人情况）:', json_sources(row.context['facts']),
                      '依据资料（资料观点仍需判断，不自动成为事实）:',
                      json_sources(row.sources)])
    if len(text) > 16000:
        raise ProcessingError('BRAIN_DOCUMENT_TOO_LARGE', '深入学习快照超过上限，未截断原文。', False)
    label = f'creator-atlas-reflection:{row.id}:' + hashlib.sha256(text.encode()).hexdigest()
    with httpx.Client(base_url=brain_url(), trust_env=False, timeout=900, follow_redirects=False) as client:
        verified = verify_document(client, label, text)
        if not verified:
            request(client, 'POST', '/ingest/knowledge', json={'text': text, 'source_name': label, 'dataset': DATASET, 'chunk_size': 512})
            verified = verify_document(client, label, text)
        if not verified:
            raise ProcessingError('BRAIN_IMPORT_UNCONFIRMED', '深入学习快照尚未通过来源核对。', False)
    assert_owner(db, job)
    job.payload = {**job.payload, 'documents': {row.id: verified}}
    job.state, job.last_error_code, job.last_error_message = 'completed', None, None
    db.commit()


def json_sources(sources):
    import json
    return json.dumps(sources, ensure_ascii=False)
