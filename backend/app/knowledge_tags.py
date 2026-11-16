"""Inspectable topic labels derived from knowledge text, without changing sources."""
import re
from collections import Counter

from sqlalchemy import or_, select

from .models import Knowledge, Work

# Reuse the topic vocabulary of the neuron library, with a separate brand label.
DEFINITIONS = [
    ('ui_design', 'UI设计', r'(?<![a-z])(?:ui|ux|figma|dashboard)(?![a-z])|界面|交互|动效|侧边栏|弹窗|导航设计|网页|网站设计|页面布局|设计系统|组件设计|配色|字体排版'),
    ('game_design', '游戏设计', r'游戏|关卡|玩法|数值策划|战斗系统|像素角色|(?<![a-z])(?:games?|gameplay|godot|unity|unreal|rpg)(?![a-z])'),
    ('ai', 'AI知识', r'(?<![a-z])(?:ai|llm|agents?|mcp|embedding|gpt|qwen|claude|gemini|codex|ollama|prompt|comfyui)(?![a-z])|人工智能|大模型|语言模型|提示词|智能体|机器学习|深度学习|模型推理|向量检索'),
    ('software', '编程开发', r'编程|代码|开发|前端|后端|数据库|爬虫|版本控制|(?<![a-z])(?:python|javascript|typescript|react|fastapi|api|docker|git|sql|sqlite|neo4j|scrapling)(?![a-z])'),
    ('visual', '3D与视觉', r'三维|建模|渲染|材质|灯光|视觉设计|平面设计|海报|运镜|分镜|电影风格|(?<![a-z])(?:3d|blender|three\.js|webgl|vfx)(?![a-z])'),
    ('brand', 'LOGO与品牌', r'标志设计|品牌|商标|字标|(?<![a-z])(?:logo|logotype|branding)(?![a-z])'),
    ('product', '产品设计', r'产品设计|产品经理|需求定义|需求分析|用户研究|用户旅程|功能规划|商业模式|产品定位|(?<![a-z])(?:prd|product design)(?![a-z])'),
    ('growth', '运营增长', r'运营|营销|增长|获客|转化率|商业化|流量|广告|短视频|口播|字幕|(?<![a-z])(?:marketing|growth|seo)(?![a-z])'),
    ('data', '数据分析', r'数据分析|数据可视化|统计分析|指标体系|数据清洗|数据报表|数据仓库|(?<![a-z])(?:analytics|pandas|power bi|tableau)(?![a-z])'),
]
LABELS = {key: label for key, label, _ in DEFINITIONS} | {'uncategorized': '待分类'}
PATTERNS = {key: '(?i)' + pattern for key, _, pattern in DEFINITIONS}


def labels(title, content, work_title=''):
    text = '\n'.join((title, content, work_title))
    keys = [key for key, pattern in PATTERNS.items() if re.search(pattern, text)] or ['uncategorized']
    return [{'id': key, 'label': LABELS[key]} for key in keys]


def condition(key, *, inherit_work=False):
    text = Knowledge.title + '\n' + Knowledge.content_zh
    if inherit_work:
        text = text + '\n' + Work.title
    matches = {key: text.regexp_match(pattern) for key, pattern in PATTERNS.items()}
    return ~or_(*matches.values()) if key == 'uncategorized' else matches[key]


def catalog(db):
    rows = db.execute(select(Knowledge.title, Knowledge.content_zh, Work.title).join(Work, Work.id == Knowledge.work_id)
                      .where(Work.processing_state == 'completed', Knowledge.analysis_id == Work.current_analysis_id))
    counts = Counter(tag['id'] for row in rows for tag in labels(*row))
    from .reflections import records
    counts.update(tag['id'] for item, _, _ in records(db) for tag in item['tags'])
    return {'items': [{'id': key, 'label': label, 'count': counts[key]} for key, label in LABELS.items()],
            'method': 'content_rules'}
