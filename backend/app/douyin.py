"""Project-owned, content-only Douyin collector using an ordinary local browser."""
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin

import httpx
from .adapters import ProcessingError, content_whitelist, parse_profile
from .config import ROOT, settings

_BROWSER_LOCK = threading.Lock()
_WORK_ID = re.compile(r'\d{10,25}')
_PLATFORM_HOSTS = {'v.douyin.com', 'www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'm.douyin.com'}
_MESSAGES = {
    'PLATFORM_LOGIN_REQUIRED': '继续读取历史作品需要登录项目独立的抖音会话。',
    'PLATFORM_VERIFICATION_REQUIRED': '平台要求人工验证，采集已停止。',
    'PLATFORM_ACCESS_RESTRICTED': '平台限制了当前会话，采集已停止。',
    'PLATFORM_CONTENT_PRIVATE': '该作品不是公开作品，未采集。',
    'PLATFORM_CONTENT_UNAVAILABLE': '作品已失效或当前会话不可访问。',
    'PLATFORM_EMPTY_RESPONSE': '平台返回空响应，不能据此认定作品已删除或历史已结束。',
    'PLATFORM_CURSOR_STALLED': '平台分页游标未前进，已停止以避免重复扫描。',
    'PLATFORM_CURSOR_UNAVAILABLE': '当前网页未返回所需历史分页，覆盖范围无法证明。',
    'PLATFORM_SCHEMA_CHANGED': '平台响应结构发生变化，未保存不完整结果。',
    'PLATFORM_RATE_LIMITED': '平台要求限流，稍后按指定间隔重试。',
    'PLATFORM_BROWSER_TIMEOUT': '浏览器未在限定时间内返回作品资料，稍后重试。',
    'BROWSER_RUNTIME_MISSING': '采集运行环境不可用，请检查 Node、项目 Playwright 依赖及浏览器。',
}


class BrowserDouyinAdapter:
    platform = 'douyin'

    def __init__(self):
        self._cache = {}

    def _enabled(self):
        if not settings.platform_requests_enabled or settings.adapter_douyin_mode != 'browser':
            raise ProcessingError('ACCESS_NOT_CONFIGURED', '抖音本机采集尚未启用。', False, severe=True)

    def _request(self, action, identity, cursor=None):
        self._enabled()
        node = shutil.which(settings.douyin_node_path)
        if not node:
            raise ProcessingError('BROWSER_RUNTIME_MISSING', _MESSAGES['BROWSER_RUNTIME_MISSING'], False, severe=True)
        if not _BROWSER_LOCK.acquire(timeout=5):
            raise ProcessingError('PLATFORM_BROWSER_BUSY', '本机采集会话正在使用，稍后重试。')
        try:
            # ponytail: one browser per process; use a shared browser service if
            # capture is later deployed across multiple worker processes.
            command = [node, str(ROOT / 'scripts' / 'douyin-browser.cjs')]
            request = {'action': action, 'id': identity, 'cursor': cursor,
                       'channel': settings.douyin_browser_channel, 'timeout': settings.douyin_browser_timeout,
                       'session': str(settings.douyin_session_file.resolve())}
            result = subprocess.run(command, input=json.dumps(request), text=True, encoding='utf-8',
                                    capture_output=True, timeout=settings.douyin_browser_timeout + 20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if len(result.stdout) > 8 * 1024 * 1024:
                raise ProcessingError('PLATFORM_RESPONSE_TOO_LARGE', '平台响应超过安全上限。', False)
            try:
                response = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise ProcessingError('BROWSER_RUNTIME_MISSING', _MESSAGES['BROWSER_RUNTIME_MISSING'], False, severe=True) from None
            if not isinstance(response, dict):
                raise ProcessingError('PLATFORM_RESPONSE_INVALID', '采集器未返回有效结果。', False)
            if not response.get('ok'):
                error = response.get('error') or {}
                code = str(error.get('code', 'PLATFORM_BROWSER_FAILED'))
                if not re.fullmatch(r'[A-Z_]{3,80}', code):
                    code = 'PLATFORM_BROWSER_FAILED'
                delay = error.get('retry_after', 0)
                raise ProcessingError(code, _MESSAGES.get(code, '平台采集失败，原始响应未写入日志。'),
                                      error.get('retryable') is True, delay if isinstance(delay, int) else 0,
                                      severe=error.get('retryable') is not True)
            if not isinstance(response.get('result'), dict):
                raise ProcessingError('PLATFORM_RESPONSE_INVALID', '采集器未返回有效资料。', False)
            return response['result']
        except (OSError, subprocess.SubprocessError):
            raise ProcessingError('PLATFORM_BROWSER_FAILED', '本机采集进程暂时不可用，原始错误未记录。') from None
        finally:
            _BROWSER_LOCK.release()

    def validate_creator(self, url):
        return parse_profile(url, 'douyin')

    def normalize_source_url(self, supplied_url):
        self._enabled()
        match = re.search(r'https://(?:v\.douyin\.com|www\.douyin\.com|douyin\.com|www\.iesdouyin\.com|m\.douyin\.com)/[^\s<>"\]]+', supplied_url)
        if not match:
            raise ProcessingError('SOURCE_URL_INVALID', '请提供抖音 HTTPS 视频链接或含链接的分享文案。', False)
        original = match.group(0).rstrip('。，,;；!！)）')
        current = original
        try:
            with httpx.Client(trust_env=False, timeout=15, follow_redirects=False) as client:
                for _ in range(6):
                    parts = urlsplit(current)
                    if parts.scheme != 'https' or parts.hostname not in _PLATFORM_HOSTS or parts.port not in {None, 443} or parts.username or parts.password:
                        raise ProcessingError('SOURCE_URL_INVALID', '链接跳转到了未允许的平台地址。', False)
                    identity = re.search(r'/(?:share/)?(?:video|note)/(\d{10,25})(?:/|$)', parts.path)
                    if identity:
                        return {'original_url': original, 'canonical_url': current,
                                'platform_work_id': identity.group(1), 'link_validation': 'identity_resolved'}
                    if parts.hostname != 'v.douyin.com' and not parts.path.startswith('/share/'):
                        raise ProcessingError('SOURCE_URL_INVALID', '该链接未指向单条抖音作品。', False)
                    with client.stream('GET', current) as response:
                        if response.status_code == 429:
                            raise ProcessingError('PLATFORM_RATE_LIMITED', _MESSAGES['PLATFORM_RATE_LIMITED'], True, 600)
                        if response.status_code in {401, 403}:
                            raise ProcessingError('PLATFORM_ACCESS_RESTRICTED', _MESSAGES['PLATFORM_ACCESS_RESTRICTED'], False, severe=True)
                        location = response.headers.get('location')
                        if response.status_code not in {301, 302, 303, 307, 308} or not location:
                            raise ProcessingError('SOURCE_URL_UNRESOLVED', '分享链接未返回可验证的作品地址。', False)
                        current = urljoin(current, location)
        except httpx.HTTPError:
            raise ProcessingError('PLATFORM_NETWORK_FAILED', '抖音分享链接暂时无法连接。') from None
        raise ProcessingError('SOURCE_URL_UNRESOLVED', '分享链接跳转次数超出上限。', False)

    def list_creator_works(self, creator_ref, cursor=None):
        if not re.fullmatch(r'[\w-]{8,200}', creator_ref) or (cursor is not None and not re.fullmatch(r'\d{1,20}', cursor)):
            raise ProcessingError('COLLECTOR_INPUT_INVALID', '创作者标识或游标无效。', False)
        page = self._request('list', creator_ref, cursor)
        if not isinstance(page.get('works'), list) or 'next_cursor' not in page or not isinstance(page.get('coverage_state'), str):
            raise ProcessingError('PLATFORM_SCHEMA_CHANGED', _MESSAGES['PLATFORM_SCHEMA_CHANGED'], False)
        result = []
        for item in page['works']:
            if not isinstance(item, dict) or not _WORK_ID.fullmatch(str(item.get('platform_work_id', ''))):
                raise ProcessingError('PLATFORM_SCHEMA_CHANGED', _MESSAGES['PLATFORM_SCHEMA_CHANGED'], False)
            result.append({'platform_work_id': item['platform_work_id'], 'content_type': item.get('content_type')})
        next_cursor = page['next_cursor']
        if next_cursor is not None and (not isinstance(next_cursor, str) or not re.fullmatch(r'\d{1,20}', next_cursor) or next_cursor == str(cursor or '0')):
            raise ProcessingError('PLATFORM_CURSOR_STALLED', _MESSAGES['PLATFORM_CURSOR_STALLED'], False)
        return {'works': result, 'next_cursor': next_cursor, 'coverage_state': page['coverage_state']}

    def fetch_work_detail(self, work_ref):
        if not _WORK_ID.fullmatch(work_ref):
            raise ProcessingError('COLLECTOR_INPUT_INVALID', '作品标识无效。', False)
        raw = self._request('detail', work_ref)
        if raw.get('platform_work_id') != work_ref or raw.get('content_type') not in {'video', 'image'}:
            raise ProcessingError('ADAPTER_IDENTITY_INVALID', '返回作品身份或类型不一致。', False)
        original = raw.get('original_url', '')
        parts = urlsplit(original)
        if parts.scheme != 'https' or parts.hostname not in _PLATFORM_HOSTS or not re.search(r'/(video|note)/' + re.escape(work_ref) + r'(?:/|$)', parts.path):
            raise ProcessingError('ADAPTER_IDENTITY_INVALID', '作品实际页面地址与作品身份不一致。', False)
        timestamp = raw.get('published_at')
        try:
            published = datetime.fromtimestamp(timestamp, timezone.utc) if isinstance(timestamp, int) and not isinstance(timestamp, bool) and timestamp > 0 else None
        except (ValueError, OSError, OverflowError):
            raise ProcessingError('PLATFORM_SCHEMA_CHANGED', '作品发布时间无效。', False) from None
        media = raw.get('media')
        if not isinstance(media, list) or not media or any(not isinstance(item, dict) or not isinstance(item.get('urls'), list) or not item['urls'] for item in media):
            raise ProcessingError('PLATFORM_MEDIA_UNAVAILABLE', '作品未返回可用媒体地址。', False)
        self._cache[work_ref] = [{'asset_type': item.get('asset_type'), 'order': order,
                                 'urls': [url for url in item['urls'] if isinstance(url, str)]}
                                for order, item in enumerate(media)]
        return content_whitelist({'platform': 'douyin', 'platform_work_id': work_ref, 'original_url': original,
                                  'title': raw.get('title', ''), 'body_text': raw.get('body_text', ''),
                                  'hashtags': raw.get('hashtags', []), 'content_type': raw['content_type'], 'published_at': published})

    def obtain_authorized_media(self, work_ref):
        self._enabled()
        if work_ref not in self._cache:
            self.fetch_work_detail(work_ref)
        return self._cache[work_ref]

    def classify_error(self, error):
        return error.code if isinstance(error, ProcessingError) else 'PLATFORM_BROWSER_FAILED'
