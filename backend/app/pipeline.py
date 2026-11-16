import json
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select, update
from .adapters import adapter_for, content_whitelist, ProcessingError
from .config import settings
from .db import now, utc
from .models import Creator, Scan, Work, Asset, Analysis, Segment, Knowledge, Embedding, Job
from .media import demo_media, prepare_video, clean_video
from .providers import provider_for, tokens
from .schemas import KnowledgeOutput
from .knowledge import link_knowledge, daily_report

STATES = {'pending_capture', 'capturing', 'pending_analysis', 'analyzing', 'completed', 'failed_retryable'}


def enqueue(db, key, job_type, creator_id=None, work_id=None, payload=None):
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        return existing
    job = Job(idempotency_key=key, job_type=job_type, stage=job_type,
              creator_id=creator_id, work_id=work_id, payload=payload or {})
    db.add(job)
    db.flush()
    return job


def enqueue_scan(db, creator, scan_type='incremental'):
    active = db.scalar(select(Scan).where(Scan.creator_id == creator.id, Scan.scan_type == scan_type,
                                        Scan.status.in_(['queued', 'running', 'waiting'])))
    if active:
        return active
    from sqlalchemy.exc import IntegrityError
    try:
        with db.begin_nested():
            scan = Scan(creator_id=creator.id, scan_type=scan_type)
            db.add(scan)
            db.flush()
            enqueue(db, f'scan:{scan.id}', 'scan', creator.id, payload={'scan_id': scan.id})
    except IntegrityError:
        scan = db.scalar(select(Scan).where(Scan.creator_id==creator.id, Scan.scan_type==scan_type,
                                           Scan.status.in_(['queued','running','waiting'])))
        if not scan:
            raise
    return scan


def assert_owner(db, job):
    db.expire(job)
    row = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
    if row.state != 'running' or row.lease_token != job._fence or utc(row.lease_expires_at) <= now():
        raise ProcessingError('LEASE_LOST', '处理租约已失效，结果未提交。', False)


def enqueue_link_capture(db, share_text):
    from .adapters import DouyinAdapter
    link = DouyinAdapter().normalize_source_url(share_text)
    existing = db.scalar(select(Work).where(Work.platform == 'douyin', Work.platform_work_id == link['platform_work_id']))
    if existing:
        return {'work_id': existing.id, 'existing': True, 'processing_state': existing.processing_state}
    creator = db.scalar(select(Creator).where(Creator.normalized_profile_url == 'capture:douyin'))
    if not creator:
        creator = Creator(platform='douyin', profile_url='', normalized_profile_url='capture:douyin',
                          display_name='链接采集 · 抖音', source_mode='real', tracking_state='stopped',
                          access_state='single_capture', history_scan_state='not_applicable')
        db.add(creator)
        db.flush()
        creator.next_check_at = None
    work = Work(creator_id=creator.id, platform='douyin', platform_work_id=link['platform_work_id'],
                original_url=link['original_url'], canonical_url=link['canonical_url'], title='等待获取作品内容',
                content_type='video', source_mode='real', processing_state='pending_capture',
                rights_statement='用户主动提交的公开作品链接；通过项目独立浏览器会话访问，受平台实际访问条件约束。')
    db.add(work)
    db.flush()
    job = enqueue(db, 'capture:' + work.id, 'capture', creator.id, work.id, {'single_capture': True})
    db.commit()
    return {'work_id': work.id, 'job_id': job.id, 'existing': False, 'processing_state': work.processing_state}


def scan_creator(db, job):
    creator = db.get(Creator, job.creator_id)
    scan = db.get(Scan, job.payload['scan_id'])
    if creator.tracking_state != 'tracking':
        scan.status = 'canceled'
        job.state = 'canceled'
        db.commit()
        return
    adapter = adapter_for(creator.platform, creator.source_mode)
    scan.status = 'running'
    db.commit()
    seen_cursors = {scan.cursor}
    while True:
        page = adapter.list_creator_works(creator.platform_creator_id, scan.cursor)
        if page['next_cursor'] is not None and page['next_cursor'] in seen_cursors:
            raise ProcessingError('PLATFORM_CURSOR_STALLED', '分页游标重复，停止扫描以避免无限循环。', False, severe=True)
        assert_owner(db, job)
        db.refresh(creator)
        if creator.tracking_state != 'tracking':
            scan.status = 'canceled'
            job.state = 'canceled'
            db.commit()
            return
        for descriptor in page['works']:
            identity = str(descriptor['platform_work_id'])
            work = db.scalar(select(Work).where(Work.platform == creator.platform, Work.platform_work_id == identity))
            if work:
                scan.known_count += 1
                continue  # 已发现作品仅核对 ID，不重新访问旧正文。
            work = Work(creator_id=creator.id, platform=creator.platform, platform_work_id=identity,
                        original_url='', title='等待获取作品内容', source_mode=creator.source_mode)
            db.add(work)
            db.flush()
            enqueue(db, f'capture:{work.id}', 'capture', creator.id, work.id)
            scan.discovered_count += 1
        scan.cursor = page['next_cursor']
        seen_cursors.add(scan.cursor)
        scan.coverage_state = page['coverage_state']
        db.commit()  # 每页事务同时提交作品与游标，崩溃可从此处恢复。
        if scan.cursor is None:
            break
    assert_owner(db, job)
    scan.status = 'completed'
    scan.finished_at = now()
    creator.last_check_at = now()
    creator.access_state = 'mock' if creator.source_mode == 'mock' else 'available'
    if scan.scan_type == 'history':
        creator.history_scan_state = 'mock_fixture_end' if creator.source_mode == 'mock' else scan.coverage_state
    job.state = 'completed'
    db.commit()


def capture(db, job):
    work = db.get(Work, job.work_id)
    if work.processing_state == 'completed':
        job.state = 'completed'
        db.commit()
        return
    creator = db.get(Creator, work.creator_id)
    if creator.tracking_state != 'tracking' and not job.payload.get('single_capture'):
        job.state = 'canceled'
        db.commit()
        return
    work.processing_state = 'capturing'
    work.version += 1
    db.commit()
    adapter = adapter_for(work.platform, work.source_mode)
    content = content_whitelist(adapter.fetch_work_detail(work.platform_work_id))
    assert_owner(db, job)
    if job.payload.get('batch_id') and content.get('content_type') != 'video':
        raise ProcessingError('BATCH_NOT_VIDEO', '详情未确认是视频，已跳过下载和模型解析。', False)
    if content.get('platform') != work.platform or str(content.get('platform_work_id')) != work.platform_work_id:
        raise ProcessingError('ADAPTER_IDENTITY_INVALID', '采集返回的平台或作品身份不一致。', False, severe=True)
    from .adapters import safe_url
    safe_url(content['original_url'])
    for key in ['original_url', 'title', 'body_text', 'hashtags', 'content_type']:
        if key in content:
            if key == 'original_url' and job.payload.get('single_capture') and work.original_url:
                work.canonical_url = content[key]
            else:
                setattr(work, key, content[key])
    work.published_at = now() if work.source_mode == 'mock' else content.get('published_at')
    work.link_validation = 'mock' if work.source_mode == 'mock' else 'not_checked'
    if work.source_mode == 'mock':
        demo_media(db, work)
    else:
        from .downloads import capture_media
        capture_media(db, work, adapter.obtain_authorized_media(work.platform_work_id))
        assert_owner(db, job)
        work.link_validation = 'page_identity_verified'
    work.processing_state = 'pending_analysis'
    enqueue(db, f'analyze:{work.id}:initial', 'analyze', creator.id, work.id,
            {'batch_id': job.payload['batch_id']} if job.payload.get('batch_id') else None)
    job.state = 'completed'
    db.commit()


def validate_output(output, source):
    result = KnowledgeOutput.model_validate(output)
    if (result.work_id, result.platform, result.source_url) != (source['id'], source['platform'], source['original_url']):
        raise ProcessingError('AI_REFERENCE_INVALID', '解析结果的作品身份或来源与输入不一致。')
    if result.transcript_segments != KnowledgeOutput.model_validate({**output, 'transcript_segments': source.get('transcript_segments', [])}).transcript_segments:
        raise ProcessingError('AI_TRANSCRIPT_CHANGED', '知识提取不得改写原始语音转写。')
    images = {x['id'] for x in source.get('images', [])}
    evidence = {'body:0': source['body_text'], 'title:0': source['title']}
    for i, segment in enumerate(result.transcript_segments):
        if segment.end_ms < segment.start_ms:
            raise ProcessingError('AI_TIME_INVALID', '转写片段时间无效。')
        evidence[f'transcript:{i}'] = segment.text_original
    for i, segment in enumerate(result.ocr_segments):
        if segment.image_ref not in images:
            raise ProcessingError('AI_REFERENCE_INVALID', 'OCR 引用了不存在的图片。')
        evidence[f'ocr:{i}'] = segment.text_original
    for i, segment in enumerate(result.visual_segments):
        if segment.frame_ref not in images or segment.end_ms < segment.start_ms:
            raise ProcessingError('AI_REFERENCE_INVALID', '画面引用或时间无效。')
        evidence[f'visual:{i}'] = segment.description_zh
    if [x.model_dump() for x in result.ocr_segments] != source.get('ocr_segments', []) or [x.model_dump() for x in result.visual_segments] != source.get('visual_segments', []):
        raise ProcessingError('AI_EVIDENCE_CHANGED', '知识提取不得改写前置 OCR 或画面证据。')
    for item in result.knowledge_items:
        if not all(ref in evidence for ref in item.source_segment_refs):
            raise ProcessingError('AI_REFERENCE_INVALID', '知识引用了不存在的原文片段。')
        if not item.content_original or not any(item.content_original in evidence[ref] for ref in item.source_segment_refs):
            raise ProcessingError('AI_QUOTE_INVALID', '知识原文依据无法在引用片段中逐字匹配。')
        if item.claim_kind=='source_claim' and all(ref.startswith('visual:') for ref in item.source_segment_refs):
            raise ProcessingError('AI_CLAIM_INVALID', '仅依据 AI 画面描述的结论需标记推论。')
    # 展示字段从已有证据的知识类型派生，避免无引用的自由文本冒充作者观点。
    result.creator_thoughts = [x.content_zh for x in result.knowledge_items if x.type=='opinion']
    result.expression_style = [x.content_zh for x in result.knowledge_items if x.type=='style']
    result.action_steps = [x.content_zh for x in result.knowledge_items if x.type=='step']
    return result


def analyze(db, job):
    work = db.get(Work, job.work_id)
    reanalysis = job.payload.get('reanalysis', False)
    if work.processing_state == 'completed' and not reanalysis:
        job.state = 'completed'
        db.commit()
        return
    provider = provider_for(work.source_mode)
    work.processing_state = 'analyzing'
    run = Analysis(work_id=work.id, provider=provider.name, model=provider.model)
    db.add(run)
    db.commit()
    context = dict(work.media_context or {})
    if work.temporary_video and 'audio_files' not in context:
        context = prepare_video(db, work)
        work.media_context = dict(context)
        db.commit()
    assets = db.scalars(select(Asset).where(Asset.work_id == work.id, Asset.asset_type.in_(['image', 'keyframe'])).order_by(Asset.source_order)).all()
    context['images'] = [{'id': x.id, 'path': str(settings.data_dir / x.file_path),
                          'time_ms': next((i.get('time_ms', 0) for i in context.get('images', []) if i['id'] == x.id), 0)} for x in assets]
    transcript = context.get('transcript_segments', [])
    if 'transcript_segments' not in context:
        transcript = []
        audio_segment_ms = context.get('audio_segment_ms', 600000)
        for order, audio in enumerate(context.get('audio_files', [])):
            if not Path(audio).exists():
                raise ProcessingError('AUDIO_CACHE_MISSING', '音频缓存缺失，请检查临时媒体。')
            text = provider.transcribe_audio(audio, None).get('text', '')
            transcript.append({'start_ms': order*audio_segment_ms, 'end_ms': min((order+1)*audio_segment_ms, work.duration_ms or (order+1)*audio_segment_ms),
                               'text_original': text, 'text_zh': text})
        context['transcript_segments'] = transcript
        work.media_context = dict(context)
        db.commit()
    if 'ocr_segments' not in context:
        context.update(provider.describe_visuals(context['images'], {}))
        work.media_context = dict(context)
        db.commit()
    source = {'id': work.id, 'platform': work.platform, 'content_type': work.content_type, 'original_url': work.original_url, 'title': work.title,
              'body_text': work.body_text, 'hashtags': work.hashtags, 'images': [{k:v for k,v in x.items() if k != 'path'} for x in context['images']],
              'transcript_segments': transcript, 'transcript_text': '\n'.join(x['text_original'] for x in transcript),
              'ocr_segments': context.get('ocr_segments', []), 'visual_segments': context.get('visual_segments', []),
              'background_audio_note': context.get('background_audio_note', ''),
              'reanalysis_limit': '本次没有可用原视频，仅使用输入中实际提供的标题、正文、转写、原图或关键帧。'
              '不得根据原视频缺失推断其曾被下载、删除或清理；声画片段为空时明确未验证视频内容。' if not work.temporary_video else ''}
    output = validate_output(provider.extract_knowledge(source), source)
    vectors = provider.embed_texts([x.title + '\n' + x.content_zh for x in output.knowledge_items])
    if len(vectors) != len(output.knowledge_items) or any(len(v) != settings.ai_embedding_dimension for v in vectors):
        raise ProcessingError('AI_EMBEDDING_INVALID', '知识索引向量数量或维度不匹配。')
    assert_owner(db, job)
    refs = {}
    for kind, segments in [('transcript', output.transcript_segments), ('ocr', output.ocr_segments), ('visual', output.visual_segments)]:
        for order, entry in enumerate(segments):
            value = entry.model_dump()
            raw = value.get('text_original', value.get('description_zh', ''))
            row = Segment(work_id=work.id, analysis_id=run.id, segment_type=kind, raw_text=raw,
                          zh_text=value.get('text_zh', raw), start_ms=value.get('start_ms'), end_ms=value.get('end_ms'),
                          image_asset_id=value.get('image_ref', value.get('frame_ref')), sequence_no=order,
                          confidence=value.get('confidence', 1))
            db.add(row)
            db.flush()
            refs[f'{kind}:{order}'] = row.id
    for kind, text in [('body', work.body_text), ('title', work.title)]:
        row = Segment(work_id=work.id, analysis_id=run.id, segment_type=kind, raw_text=text, zh_text=text, sequence_no=0)
        db.add(row)
        db.flush()
        refs[f'{kind}:0'] = row.id
    items = []
    for item, vector in zip(output.knowledge_items, vectors):
        row = Knowledge(work_id=work.id, analysis_id=run.id, item_type=item.type, title=item.title, content_zh=item.content_zh,
                        content_original=item.content_original, claim_kind=item.claim_kind, confidence=item.confidence,
                        source_segment_ids=[refs[ref] for ref in item.source_segment_refs], tokens=' '.join(tokens(item.title + ' ' + item.content_zh)))
        db.add(row)
        db.flush()
        db.add(Embedding(knowledge_item_id=row.id, embedding_model=provider.embedding_model,
                         embedding_dimension=len(vector), embedding=vector))
        items.append(row)
    run.output = output.model_dump()
    run.status = 'success'
    run.completed_at = now()
    work.processing_state = 'completed'
    work.current_analysis_id = run.id
    work.media_context = {**context, 'provider': provider.name}
    work.version += 1
    job.state = 'completed'
    db.flush()
    link_knowledge(db, items)
    if work.temporary_video:
        enqueue(db, f'cleanup:{work.id}', 'cleanup', work.creator_id, work.id)
    if settings.brain_sync_enabled and work.source_mode != 'mock' and provider.name != 'mock':
        from .brain_sync import enqueue_work
        sync_job = enqueue_work(db, work)
        if sync_job and job.payload.get('batch_id'):
            sync_job.payload = {**sync_job.payload, 'batch_id': job.payload['batch_id']}
    db.commit()  # 解析、片段、知识、向量、状态在同一个事务提交；原视频清理在后续独立作业执行。
    if settings.reflection_enabled and work.source_mode in {'real', 'manual'} and provider.name != 'mock':
        from .reflections import enqueue_reflection
        try:
            enqueue_reflection(db, work)
            db.commit()
        except Exception:
            # Optional derived learning must never roll back a paid source analysis.
            db.rollback()
            from .jobs import alert_once
            alert_once(db, 'reflection_setup_failure', '原知识已入库，深入学习排队未完成；请检查学习背景与本机配置。')
            db.commit()


def execute_job(db, job):
    if job.payload.get('batch_id'):
        from .night_batch import before_job
        if not before_job(db, job):
            return
    if job.job_type == 'night_batch':
        from .night_batch import select_videos
        select_videos(db, job)
    elif job.job_type == 'scan':
        scan_creator(db, job)
    elif job.job_type == 'capture':
        capture(db, job)
    elif job.job_type == 'analyze':
        analyze(db, job)
    elif job.job_type == 'reflect':
        from .reflections import process
        process(db, job)
    elif job.job_type == 'cleanup':
        clean_video(db, db.get(Work, job.work_id))
        assert_owner(db, job)
        job.state = 'completed'
        db.commit()
    elif job.job_type == 'brain_sync':
        from .brain_sync import sync
        sync(db, job)
    elif job.job_type == 'report':
        assert_owner(db, job)
        daily_report(db, job.payload['date'])
        job.state = 'completed'
        db.commit()
    elif job.job_type == 'backup':
        from .backup import create_backup
        assert_owner(db, job)
        db.commit()
        create_backup()
        assert_owner(db, job)
        job.state = 'completed'
        db.commit()
    else:
        raise ProcessingError('JOB_TYPE_INVALID', '未知作业类型。', False)
