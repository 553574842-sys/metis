import math
from datetime import datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, cast, Text, or_
from .config import settings
from .db import now, utc
from . import knowledge_tags
from .adapters import ProcessingError
from .models import Analysis, Asset, Knowledge, Work, Creator, Embedding, Relation, Report, Segment
from .providers import provider_for, tokens, mock_embedding

# Keep source tokens verbatim; normalize equivalent wording only while searching.
TERM_GROUPS = [('服装', '衣物', '衣服', '服饰', '衣装'), ('网站', '网页', '站点')]
TERM_CANONICAL = {word: group[0] for group in TERM_GROUPS for word in group}
TERM_ALIASES = {word: group for group in TERM_GROUPS for word in group}
VISUAL_RELATED = {
    '空间感': ('立体', '透视', '纵深', '立方体', '错位', '折面'),
    '立体': ('空间感', '透视', '纵深', '立方体'),
    '立体几何': ('立方体', '空间几何', '折面'),
    '黑白': ('单色', '黑底白', '白底黑', '背景上有黑色', '背景上的黑色', '黑色粗体'),
    '块面': ('色块', '几何块', '实心图形', '半圆', '多边形'),
}
VISUAL_GENERIC = {'logo', '标志', '字标', '案例', '设计', '图标', '图片', '几个', '什么'}


@lru_cache(maxsize=2048)
def reflection_words(text):
    # The text is the cache key, so edits and new reflections are searchable immediately.
    return frozenset(TERM_CANONICAL.get(word, word) for word in tokens(text))


def similarity(vector, stored):
    if vector is None or len(vector) != len(stored):
        return 0.0
    return math.sumprod(vector, stored) / ((math.hypot(*vector) or 1) * (math.hypot(*stored) or 1))


def evidence_record(item, work, creator, relevance=1.0):
    return {'knowledge_item_id': item.id, 'work_id': work.id, 'creator_id': creator.id, 'title': item.title,
            'summary': item.content_zh, 'evidence_excerpt': item.content_original, 'creator': creator.display_name,
            'platform': work.platform, 'source_url': work.original_url, 'source_mode': work.source_mode,
            'analysis_mode': {'mock': 'mock', 'local': 'local'}.get(work.media_context.get('provider'), 'cloud'),
            'claim_kind': item.claim_kind, 'item_type': item.item_type, 'confidence': item.confidence,
            'source_segment_ids': item.source_segment_ids, 'published_at': work.published_at.isoformat() if work.published_at else None,
            'relevance': round(relevance, 4), 'learned_at': utc(item.created_at).isoformat(),
            'tags': knowledge_tags.labels(item.title, item.content_zh, work.title)}


def work_filters(query, filters):
    query = query.where(Work.processing_state == 'completed')
    for key, col in [('platforms', Work.platform), ('creator_ids', Creator.id)]:
        if filters.get(key):
            query = query.where(col.in_(filters[key]))
    for key, end in [('date_from', False), ('date_to', True)]:
        if filters.get(key):
            value = datetime.fromisoformat(filters[key]).replace(tzinfo=ZoneInfo(settings.app_timezone))
            query = query.where(Work.published_at < value + timedelta(days=1)) if end else query.where(Work.published_at >= value)
    return query


def filtered_query(filters):
    query = select(Knowledge, Work, Creator, Embedding).join(Work, Work.id == Knowledge.work_id).join(
        Creator, Creator.id == Work.creator_id).join(Embedding, Embedding.knowledge_item_id == Knowledge.id).where(
        Knowledge.analysis_id == Work.current_analysis_id)
    query = work_filters(query, filters)
    if filters.get('topic'):
        query = query.where(Knowledge.tokens.contains(filters['topic']))
    tags = filters.get('tags', [])
    if not isinstance(tags, list) or len(tags) > len(knowledge_tags.LABELS) or any(not isinstance(tag, str) or tag not in knowledge_tags.LABELS for tag in tags):
        raise ProcessingError('KNOWLEDGE_TAG_INVALID', '知识标签无效。', False)
    if tags:
        query = query.where(or_(*(knowledge_tags.condition(tag, inherit_work=True) for tag in tags)))
    return query


def normalize(text):
    return ''.join(text.casefold().split())


def visual_records(db, filters, *, asset_id=None):
    statement = select(Asset.id, Asset.asset_type, Asset.source_order, Work.id.label('work_id'), Work.title,
                       Work.platform, Work.original_url, Work.source_mode, Work.published_at,
                       Creator.id.label('creator_id'), Creator.display_name, Analysis.provider,
                       Analysis.completed_at, Segment.id.label('segment_id'), Segment.segment_type,
                       Segment.raw_text, Segment.zh_text, Segment.created_at).select_from(Asset).join(
        Work, Asset.work_id == Work.id).join(Creator, Creator.id == Work.creator_id).join(
        Analysis, Analysis.id == Work.current_analysis_id).join(
        Segment, (Segment.image_asset_id == Asset.id) & (Segment.analysis_id == Analysis.id) & (Segment.work_id == Work.id)
    ).where(Asset.asset_type.in_(('image', 'keyframe')), Segment.segment_type.in_(('visual', 'ocr')),
            Analysis.status == 'success')
    if asset_id is not None:
        statement = statement.where(Asset.id == asset_id)
    # ponytail: scan existing captions in memory. A full-text index can replace this when the library outgrows the scan.
    images = {}
    for row in db.execute(work_filters(statement, filters)):
        images.setdefault(row.id, []).append(row)
    for segments in images.values():
        row = segments[0]
        caption = '\n'.join(dict.fromkeys(s.zh_text for s in segments if s.segment_type == 'visual' and s.zh_text.strip()))
        ocr = '\n'.join(dict.fromkeys(s.raw_text for s in segments if s.segment_type == 'ocr' and s.raw_text.strip()))
        text = '\n'.join((caption, ocr, *(s.zh_text for s in segments if s.segment_type == 'ocr' and s.zh_text != s.raw_text)))
        if text.strip():
            yield row, segments, caption, ocr, text


def visual_record(row, segments, caption, ocr, text, score):
    kind = '原图' if row.asset_type == 'image' else '关键帧'
    return {'knowledge_item_id': 'image:' + row.id, 'work_id': row.work_id, 'creator_id': row.creator_id,
            'title': f'{kind} {row.source_order + 1} · {row.title}', 'summary': caption or ocr,
            'evidence_excerpt': ocr or caption, 'creator': row.display_name, 'platform': row.platform,
            'source_url': row.original_url, 'source_mode': row.source_mode,
            'analysis_mode': row.provider if row.provider in {'mock', 'local'} else 'cloud',
            'claim_kind': 'inference', 'item_type': 'visual', 'confidence': None,
            'source_segment_ids': sorted(s.segment_id for s in segments),
            'published_at': utc(row.published_at).isoformat() if row.published_at else None,
            'learned_at': utc(row.completed_at or row.created_at).isoformat(), 'relevance': round(score, 4),
            'tags': knowledge_tags.labels(row.title, text), 'image_asset_id': row.id}


def visual_hits(db, query, words, filters):
    # Substring matching keeps OCR names and Chinese compounds discoverable even when segmentation differs.
    terms = [word for word in words if len(word) > 1 and word not in VISUAL_GENERIC]
    related = {word: VISUAL_RELATED.get(word, ()) for word in terms}
    phrase = normalize(query)
    brand_query = 'logo' in words or '字标' in words or '标志设计' in phrase
    for row, segments, caption, ocr, text in visual_records(db, filters):
        haystack = normalize(text)
        if filters.get('topic') and normalize(filters['topic']) not in haystack:
            continue
        if len(phrase) > 1 and phrase in haystack:
            score = .98 if row.asset_type == 'image' else .92
        elif terms:
            direct = sum(word in haystack for word in terms)
            nearby = sum(word not in haystack and any(alias in haystack for alias in related[word]) for word in terms)
            if direct + nearby < min(2, len(terms)):
                continue
            weight = .77 if row.asset_type == 'image' else .70
            score = weight * (direct + .68 * nearby) / len(terms)
        elif row.asset_type == 'image' and row.source_order == 0 and len(phrase) > 1 and phrase in normalize(row.title):
            score = .99
        else:
            continue
        item = visual_record(row, segments, caption, ocr, text, score)
        tags = {tag['id'] for tag in item['tags']}
        if brand_query and 'brand' not in tags:
            continue
        if filters.get('tags') and not tags.intersection(filters['tags']):
            continue
        yield item


def search(db, query='', filters=None, limit=20, offset=0, sort='relevance', *, prefer_exact=False):
    filters = filters or {}
    statement = filtered_query(filters)
    from .reflections import records as reflection_records
    reflections = reflection_records(db, filters)
    if not query.strip():
        total = db.scalar(select(func.count()).select_from(statement.with_only_columns(Knowledge.id).subquery()))
        order = [Knowledge.created_at.asc(), Knowledge.id.asc()] if sort == 'oldest' else [Knowledge.created_at.desc(), Knowledge.id.desc()]
        rows = db.execute(statement.with_only_columns(Knowledge, Work, Creator).order_by(*order)
                          .limit(limit + offset if reflections else limit).offset(0 if reflections else offset))
        items = [evidence_record(*row) for row in rows]
        if reflections:
            items.extend(item for item, _, _ in reflections)
            items.sort(key=lambda x: (x['learned_at'], x['knowledge_item_id']), reverse=sort != 'oldest')
            items = items[offset:offset+limit]
        return {'items': items, 'total': total + len(reflections), 'mode': 'recent', 'offset': offset}
    original_words = set(tokens(query))
    words = {TERM_CANONICAL.get(word, word) for word in original_words}
    lexical_words = {alias for word in original_words for alias in TERM_ALIASES.get(word, (word,))}
    phrase = normalize(query)
    direct_match = or_(*(func.replace(func.lower(column), ' ', '').contains(phrase, autoescape=True)
                         for column in (Knowledge.title, Knowledge.content_zh)))
    exact = bool(prefer_exact and (db.scalar(select(statement.where(direct_match).exists())) or any(
        phrase in normalize(item['title']) or phrase in normalize(item['summary']) for item, _, _ in reflections)))
    embeddings_by_model = {}
    if not exact:
        embeddings_by_model['mock-hash-v1'] = mock_embedding(query)
    # A direct match can use stored text and captions without loading a model.
    if settings.ai_provider != 'mock' and not exact:
        try:
            provider = provider_for()
            embeddings_by_model[provider.embedding_model] = provider.embed_texts([query])[0]
        except ProcessingError:
            # A missing embedding service must not hide matching text and images.
            pass
    scores = statement.with_only_columns(Knowledge.id, Knowledge.title, Knowledge.content_zh, Knowledge.tokens,
                                         Knowledge.created_at)
    if embeddings_by_model:
        scores = scores.add_columns(Embedding.embedding_model, Embedding.embedding_dimension, Embedding.embedding)
    if embeddings_by_model and db.bind.dialect.name == 'postgresql' and sort == 'relevance':
        candidates = {}
        # pgvector 索引用于每个同维度模型的候选召回；中文词项 GIN 补充关键词候选。
        for model, vector in embeddings_by_model.items():
            from pgvector.sqlalchemy import Vector
            dim = len(vector)
            column = cast(Embedding.embedding, Vector(dim))
            semantic = scores.where(Embedding.embedding_model == model, Embedding.embedding_dimension == dim).order_by(column.cosine_distance(vector)).limit(200)
            for row in db.execute(semantic):
                candidates[row.id] = row
        if lexical_words:
            lexical = scores.where(or_(*[func.to_tsvector('simple', Knowledge.tokens).op('@@')(func.plainto_tsquery('simple', word)) for word in lexical_words])).limit(200)
            for row in db.execute(lexical):
                candidates[row.id] = row
        for row in db.execute(scores.where(direct_match)):
            candidates[row.id] = row
        rows = candidates.values()
    elif exact:
        rows = db.execute(scores.where(direct_match))
    else:
        # ponytail: SQLite ranks the full eligible corpus to preserve exact totals.
        rows = db.execute(scores)
    ranked = []
    word_count = max(1, len(words))
    for row in rows:
        document_words = {TERM_CANONICAL.get(word, word) for word in row.tokens.split()}
        lexical = len(words & document_words) / word_count
        vector = embeddings_by_model.get(row.embedding_model) if embeddings_by_model else None
        semantic = similarity(vector, row.embedding) if vector is not None and len(vector) == row.embedding_dimension else 0.0
        score = .65 * lexical + .35 * max(0, semantic) if not exact else 0
        if phrase in normalize(row.title):
            score = 1.0
        elif phrase in normalize(row.content_zh):
            score = .97
        if score >= .12:
            ranked.append((row.id, round(score, 4), utc(row.created_at).isoformat(), None))
    for item, model, stored_vector in reflections:
        if phrase in normalize(item['title']):
            score = 1.0
        elif phrase in normalize(item['summary']):
            score = .97
        elif exact:
            continue
        else:
            text = item['title'] + ' ' + item['summary']
            lowered = text.lower()
            # If no query term occurs in the text, segmentation cannot yield a lexical hit.
            lexical = len(words & reflection_words(text)) / word_count if any(word in lowered for word in lexical_words) else 0.0
            semantic = similarity(embeddings_by_model.get(model), stored_vector)
            score = .65 * lexical + .35 * max(0, semantic)
        if score >= .12:
            ranked.append((item['knowledge_item_id'], round(score, 4), item['learned_at'], item))
    ranked.extend((item['knowledge_item_id'], item['relevance'], item['learned_at'], item)
                  for item in visual_hits(db, query, words, filters))
    if sort in {'newest', 'oldest'}:
        ranked.sort(key=lambda row: (row[2], row[0]), reverse=sort == 'newest')
    else:
        ranked.sort(key=lambda row: (row[1], row[2], row[0]), reverse=True)
    page = ranked[offset:offset+limit]
    ids = [identity for identity, _, _, reflection in page if reflection is None]
    details = {row[0].id: row for row in db.execute(statement.with_only_columns(Knowledge, Work, Creator)
                                                   .where(Knowledge.id.in_(ids)))} if ids else {}
    items = []
    for identity, score, _, extra in page:
        if extra is not None:
            items.append(extra | {'relevance': score})
        elif identity in details:
            items.append(evidence_record(*details[identity], relevance=score))
    return {'items': items, 'total': len(ranked), 'mode': 'exact' if exact else 'hybrid', 'offset': offset}


def ask(db, question, filters):
    result = search(db, question, filters, 12)
    evidence = result['items']
    if not evidence:
        return {'answer_zh': '未找到支持这一结论的来源。请调整问题或导入相关资料。', 'status': 'insufficient_evidence',
                'citations': [], 'limitations': ['知识库中没有匹配的已完成解析。']}
    modes = {x['analysis_mode'] for x in evidence}
    provider = provider_for('mock' if modes == {'mock'} else None)
    answer = provider.answer(question, evidence)
    ids = set(answer['citation_ids'])
    return {'answer_zh': answer['answer_zh'], 'status': provider.name, 'citations': [x for x in evidence if x['knowledge_item_id'] in ids],
            'limitations': answer['limitations'] + (['引用中含有明确标记的 Mock 资料。'] if 'mock' in modes else [])}


def compare(db, question, creator_ids, filters):
    groups = []
    all_evidence = []
    for creator_id in creator_ids:
        result = search(db, question, {**filters, 'creator_ids': [creator_id]}, 6)
        creator = db.get(Creator, creator_id)
        groups.append({'creator_id': creator_id, 'creator': creator.display_name if creator else '未知账号', 'evidence': result['items']})
        all_evidence.extend(result['items'])
    if any(not x['evidence'] for x in groups):
        return {'status': 'insufficient_evidence', 'answer_zh': '部分账号缺少相关证据，无法可靠比较。',
                'groups': groups, 'citations': all_evidence, 'limitations': ['仅展示已找到的原文，未推定共同点或分歧。']}
    provider = provider_for('mock' if all(x['analysis_mode'] == 'mock' for x in all_evidence) else None)
    result = provider.answer('比较以下创作者的原观点、共同点和分歧，推论显式标明：' + question, all_evidence)
    return {'status': provider.name, 'answer_zh': result['answer_zh'], 'groups': groups, 'citations': all_evidence, 'limitations': result['limitations']}


def link_knowledge(db, items):
    # 相似度只产生 AI 推断关系；支持/矛盾/前置/延伸须用户或模型提供双方原文证据。
    existing = db.execute(select(Knowledge, Work).join(Work, Knowledge.work_id == Work.id).where(Knowledge.analysis_id == Work.current_analysis_id)).all()
    for item in items:
        a = set(item.tokens.split())
        for other, work in existing:
            if other.id == item.id or other.work_id == item.work_id:
                continue
            b = set(other.tokens.split())
            score = len(a & b) / max(1, len(a | b))
            if score >= .22:
                left, right = sorted([item.id, other.id])
                if not db.scalar(select(Relation).where(Relation.source_item_id == left, Relation.target_item_id == right, Relation.relation_type == 'similar')):
                    db.add(Relation(source_item_id=left, target_item_id=right, relation_type='similar', evidence={
                        'claim_kind': 'inference', 'source_item_id': item.id, 'target_item_id': other.id,
                        'source_excerpt': item.content_original, 'target_excerpt': other.content_original,
                        'reason': '中文词项重叠，可能涉及同一主题；尚未证明观点相同。', 'similarity': round(score, 3)}))


def daily_report(db, date_string):
    date = datetime.strptime(date_string, '%Y-%m-%d').date()
    start = datetime.combine(date, time.min, tzinfo=ZoneInfo(settings.app_timezone))
    end = start + timedelta(days=1)
    rows = db.execute(filtered_query({}).join(Analysis, Analysis.id == Knowledge.analysis_id).where(
        Analysis.completed_at >= start, Analysis.completed_at < end).order_by(Knowledge.created_at)).all()
    evidence = [evidence_record(*row[:3]) for row in rows]
    pending = db.scalar(select(func.count()).select_from(Work).where(Work.processing_state != 'completed'))
    content = {'summary_zh': '当天暂无新增知识。', 'highlights': []}
    if evidence:
        provider = provider_for('mock' if all(x['analysis_mode'] == 'mock' for x in evidence) else None)
        content = provider.synthesize_report(evidence, date_string)
    relation_count = db.scalar(select(func.count()).select_from(Relation).where(Relation.created_at >= start, Relation.created_at < end))
    content.update({'work_count': len({x['work_id'] for x in evidence}), 'knowledge_count': len(evidence),
                    'relation_count': relation_count, 'topics': sorted({token for x in evidence for token in tokens(x['title']) if len(token) > 1})[:12],
                    'opinions': [x for x in evidence if x['item_type'] == 'opinion'],
                    'action_steps': [x for x in evidence if x['item_type'] in {'step', 'method'}],
                    'contains_mock': any(x['analysis_mode'] == 'mock' for x in evidence)})
    report = db.scalar(select(Report).where(Report.report_date == date_string, Report.timezone == settings.app_timezone))
    if not report:
        report = Report(report_date=date_string, timezone=settings.app_timezone, revision=0)
        db.add(report)
    report.revision += 1
    report.status = 'partial' if pending else 'completed'
    report.generated_at = now()
    report.content_zh = content
    report.source_work_ids = list(dict.fromkeys(x['work_id'] for x in evidence))
    report.partial_reason = f'{pending} 条作品尚未完成解析，报告只包含已入库知识。' if pending else None
    db.commit()
    return report
