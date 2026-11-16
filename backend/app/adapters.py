import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from .config import settings


@dataclass
class ProcessingError(Exception):
    code: str
    message: str
    retryable: bool = True
    retry_after: int = 0
    severe: bool = False


class PlatformAdapter(Protocol):
    platform: str
    def validate_creator(self, profile_url: str) -> dict: ...
    def list_creator_works(self, creator_ref: str, cursor: str | None = None) -> dict: ...
    def fetch_work_detail(self, work_ref: str) -> dict: ...
    def obtain_authorized_media(self, work_ref: str) -> list[dict]: ...
    def normalize_source_url(self, supplied_url: str) -> dict: ...
    def classify_error(self, exc: Exception) -> str: ...


def safe_url(url: str, allow_empty=False):
    if not url and allow_empty:
        return ''
    parts = urlsplit(url.strip())
    if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('仅接受没有内嵌凭据的 HTTP/HTTPS 地址')
    if any(ord(char) < 32 for char in url):
        raise ValueError('地址包含非法控制字符')
    return url.strip()


def parse_profile(url: str, platform=None):
    safe_url(url)
    parts = urlsplit(url.strip())
    host = parts.hostname.lower()
    if host in {'www.douyin.com', 'douyin.com'}:
        match = re.fullmatch(r'/user/([\w-]+)/?', parts.path)
        detected = 'douyin'
    elif host in {'www.xiaohongshu.com', 'xiaohongshu.com'}:
        match = re.fullmatch(r'/user/profile/([\w-]+)/?', parts.path)
        detected = 'xiaohongshu'
    elif host == 'example.invalid':
        match = re.fullmatch(r'/mock/(douyin|xiaohongshu)/user/([\w-]+)/?', parts.path)
        detected = match.group(1) if match else None
    else:
        raise ValueError('请使用抖音 /user/ 或小红书 /user/profile/ 的完整主页链接。短链需先自行打开后复制主页地址。')
    if not match or (platform and platform != detected):
        raise ValueError('无法识别创作者主页或平台不匹配')
    identity = match.group(2) if host == 'example.invalid' else match.group(1)
    normalized = urlunsplit((parts.scheme, host, parts.path.rstrip('/'), '', ''))
    return {'platform': detected, 'platform_creator_id': identity, 'normalized_profile_url': normalized}


# 只允许保存内容字段；上游其他字段在日志、模型请求和持久化前丢弃。
CONTENT_FIELDS = {'platform', 'platform_work_id', 'original_url', 'title', 'body_text',
                  'hashtags', 'published_at', 'content_type'}


def content_whitelist(payload):
    result = {key: payload[key] for key in CONTENT_FIELDS if key in payload}
    result['hashtags'] = [str(x)[:100] for x in result.get('hashtags', []) if isinstance(x, str)]
    return result


TOPICS = [
    ('RAG', '给知识库建立可核验的引用', '检索增强生成的第一步是整理资料。分段时保留来源和原文。先检索证据，再生成回答。没有证据时应说明资料不足。', 'method'),
    ('编程', '用最小实验验证一个想法', '编程时先描述可以验证的结果，再写最小的实现。一次修改一个问题，运行针对性的测试，再逐步扩展。', 'method'),
    ('商业', '用访谈记录校验产品需求', '产品需求需要来自具体场景。记录用户的原话，区分行为与猜测。先验证核心任务是否成立，再增加功能。', 'opinion'),
    ('游戏开发', '游戏原型的反馈循环', '游戏原型先实现一个完整的反馈循环：输入、状态变化、反馈。通过试玩记录具体问题，逐次改进体验。', 'step'),
    ('RAG', '检索质量需要独立检查', '知识库应先检查检索是否找到了相关原文。关键词检索帮助定位术语，语义检索补充近义表达。回答中应同时保留作者与出处。', 'opinion'),
    ('AI', '把事实和推论分开记录', '整理 AI 分析时，来源明确表达的观点与模型推论需要分别标记。置信度用于说明不确定性，不能替代原文证据。', 'knowledge'),
]


class MockAdapter:
    def __init__(self, platform):
        self.platform = platform

    def validate_creator(self, profile_url):
        return parse_profile(profile_url, self.platform)

    def list_creator_works(self, creator_ref, cursor=None):
        offset = int(cursor or 0)
        indices = list(range(6))[offset:offset + 2]
        return {'works': [{'platform_work_id': f'demo-{creator_ref}-{i}', 'index': i} for i in indices],
                'next_cursor': str(offset + 2) if offset + 2 < 6 else None, 'coverage_state': 'mock_fixture_end'}

    def fetch_work_detail(self, work_ref):
        index = int(work_ref.rsplit('-', 1)[1])
        topic, title, body, _ = TOPICS[index]
        return content_whitelist({'platform': self.platform, 'platform_work_id': work_ref,
                                  'original_url': f'https://example.invalid/mock/{self.platform}/work/{work_ref}',
                                  'title': title, 'body_text': body, 'hashtags': [topic],
                                  'content_type': 'video' if index == 0 else 'image' if index == 1 else 'text'})

    def obtain_authorized_media(self, work_ref):
        return []  # 演示素材由本地 FFmpeg/Pillow 生成，无平台请求。

    def normalize_source_url(self, supplied_url):
        return {'original_url': safe_url(supplied_url), 'canonical_url': None, 'link_validation': 'mock'}

    def classify_error(self, exc):
        return exc.code if isinstance(exc, ProcessingError) else 'INTERNAL_ERROR'


class RestrictedAdapter(MockAdapter):
    def list_creator_works(self, creator_ref, cursor=None):
        raise ProcessingError('ACCESS_NOT_CONFIGURED', '真实平台接入尚未验证。请使用已获授权的素材手动导入。', False, severe=True)

    def fetch_work_detail(self, work_ref):
        raise ProcessingError('ACCESS_NOT_CONFIGURED', '真实作品访问未配置', False)

    def obtain_authorized_media(self, work_ref):
        raise ProcessingError('ACCESS_NOT_CONFIGURED', '真实媒体访问未配置', False)


class DouyinAdapter(RestrictedAdapter):
    def __init__(self):
        super().__init__('douyin')

    def _browser(self):
        if not settings.platform_requests_enabled or settings.adapter_douyin_mode != 'browser':
            raise ProcessingError('ACCESS_NOT_CONFIGURED', '真实平台接入尚未启用。', False, severe=True)
        from .douyin import BrowserDouyinAdapter
        if not hasattr(self, '_client'):
            self._client = BrowserDouyinAdapter()
        return self._client

    def list_creator_works(self, creator_ref, cursor=None):
        return self._browser().list_creator_works(creator_ref, cursor)

    def fetch_work_detail(self, work_ref):
        return self._browser().fetch_work_detail(work_ref)

    def obtain_authorized_media(self, work_ref):
        return self._browser().obtain_authorized_media(work_ref)

    def normalize_source_url(self, supplied_url):
        return self._browser().normalize_source_url(supplied_url)


class XiaohongshuAdapter(RestrictedAdapter):
    def __init__(self):
        super().__init__('xiaohongshu')


class ManualImportAdapter:
    def normalize_source_url(self, supplied_url):
        return {'original_url': safe_url(supplied_url, True), 'canonical_url': None,
                'link_validation': 'not_checked' if supplied_url else 'missing'}


def adapter_for(platform, source_mode=None):
    mode = source_mode or (settings.adapter_douyin_mode if platform == 'douyin' else settings.adapter_xhs_mode)
    return MockAdapter(platform) if mode == 'mock' else DouyinAdapter() if platform == 'douyin' else XiaohongshuAdapter()


def capabilities():
    rows = [{'platform': platform, 'mode': mode, 'real_status': 'not_tested',
             'capabilities': {key: 'not_tested' for key in ['creator_lookup', 'list_works', 'work_detail', 'download_image', 'temporary_video', 'authorization', 'original_link']},
             'message': 'Mock 使用本地虚构资料；真实平台权限、接口和覆盖范围尚未验证。人工导入可用。'}
            for platform, mode in [('douyin', settings.adapter_douyin_mode), ('xiaohongshu', settings.adapter_xhs_mode)]]
    if settings.adapter_douyin_mode == 'browser':
        rows[0].update(real_status='limited', message='使用本机独立浏览器会话采集公开作品；历史分页需要有效登录，限制访问时会停止。覆盖范围以每次扫描记录为准。')
        rows[0]['capabilities'].update(list_works='browser_session', work_detail='browser_session',
                                       download_image='browser_session', temporary_video='browser_session',
                                       original_link='identity_checked')
    return rows
