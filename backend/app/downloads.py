"""Bounded downloads of public platform media; never forwards browser cookies."""
import ipaddress
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError
from .adapters import ProcessingError
from .config import settings
from .media import under, check_space, add_asset

_MEDIA_DOMAINS = {'douyinvod.com', 'zjcdn.com', 'bytecdn.cn', 'bytecdn.com', 'douyinpic.com',
                  'douyinstatic.com', 'byteimg.com', 'ibytedtos.com', 'snssdk.com', 'amemv.com', 'douyin.com'}


def public_media_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in {None, 443}
                or not any(host == domain or host.endswith('.' + domain) for domain in _MEDIA_DOMAINS)):
            raise ValueError
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or not all(ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise ValueError
    except (ValueError, TypeError):
        raise ProcessingError('MEDIA_ADDRESS_REJECTED', '媒体地址未通过平台域名及公网地址检查。', False) from None
    except OSError:
        raise ProcessingError('MEDIA_NETWORK_FAILED', '媒体域名暂时无法解析。') from None
    return url


def download(url, destination, maximum_bytes=None):
    destination = Path(destination)
    under(settings.data_dir, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.capture-' + str(uuid4()) + '.part')
    maximum = settings.max_upload_mb * 1024 * 1024
    if maximum_bytes is not None:
        maximum = min(maximum, maximum_bytes)
    started = time.monotonic()
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(30, connect=10),
                          headers={'Referer': 'https://www.douyin.com/', 'User-Agent': 'Mozilla/5.0'}) as client:
            for _ in range(6):
                public_media_url(url)
                with client.stream('GET', url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            raise ProcessingError('MEDIA_RESPONSE_INVALID', '媒体跳转缺少目标地址。', False)
                        url = urljoin(url, location)
                        continue
                    if response.status_code == 429:
                        delay = response.headers.get('retry-after', '')
                        try:
                            seconds = int(delay) if delay.isdigit() else int((parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds()) + 1
                        except (ValueError, TypeError, OverflowError):
                            seconds = 0
                        raise ProcessingError('PLATFORM_RATE_LIMITED', '媒体服务要求限流，已暂停。', True, max(600, seconds))
                    if response.status_code in {401, 403}:
                        raise ProcessingError('MEDIA_ACCESS_RESTRICTED', '媒体服务拒绝当前地址，未尝试规避。', False, severe=True)
                    if response.status_code >= 400:
                        raise ProcessingError('MEDIA_HTTP_FAILED', '媒体服务请求失败。', response.status_code >= 500)
                    mime = response.headers.get('content-type', '').split(';')[0]
                    if mime in {'text/html', 'application/json'}:
                        raise ProcessingError('MEDIA_RESPONSE_INVALID', '媒体服务返回了网页或 JSON。', False)
                    length = response.headers.get('content-length', '')
                    expected = int(length) if length.isdigit() else None
                    if expected is not None and expected > maximum:
                        raise ProcessingError('MEDIA_TOO_LARGE', '媒体文件超过本机配置上限。', False)
                    total = 0
                    with temporary.open('wb') as output:
                        for chunk in response.iter_bytes(256 * 1024):
                            total += len(chunk)
                            if total > maximum:
                                raise ProcessingError('MEDIA_TOO_LARGE', '媒体文件超过本机配置上限。', False)
                            if time.monotonic() - started > 180:
                                raise ProcessingError('MEDIA_DOWNLOAD_TIMEOUT', '媒体下载超过时间上限。')
                            output.write(chunk)
                    if not total or (expected is not None and total != expected):
                        raise ProcessingError('MEDIA_INCOMPLETE', '媒体文件为空或下载不完整。')
                    temporary.replace(destination)
                    return destination
        raise ProcessingError('MEDIA_REDIRECT_LIMIT', '媒体跳转次数超出上限。', False)
    except httpx.HTTPError:
        raise ProcessingError('MEDIA_NETWORK_FAILED', '媒体传输暂时失败，未保存不完整文件。') from None
    finally:
        temporary.unlink(missing_ok=True)


def capture_media(db, work, descriptors):
    check_space()
    if work.content_type not in {'video', 'image'} or not isinstance(descriptors, list) or not descriptors or len(descriptors) > 100:
        raise ProcessingError('MEDIA_DESCRIPTION_INVALID', '媒体数量或描述无效。', False)
    if work.content_type == 'video' and (len(descriptors) != 1 or descriptors[0].get('asset_type') != 'video'):
        raise ProcessingError('MEDIA_DESCRIPTION_INVALID', '视频作品没有唯一的原视频素材。', False)
    if work.content_type == 'image' and any(item.get('asset_type') != 'image' for item in descriptors):
        raise ProcessingError('MEDIA_DESCRIPTION_INVALID', '图文作品包含了错误类型素材。', False)
    total_bytes = 0
    maximum = settings.max_upload_mb * 1024 * 1024
    for order, descriptor in enumerate(descriptors):
        check_space()
        candidates = descriptor.get('urls')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], str):
            raise ProcessingError('MEDIA_DESCRIPTION_INVALID', '作品没有可用媒体地址。', False)
        root = settings.data_dir / ('temporary_video' if work.content_type == 'video' else 'images') / work.id
        target = root / ('original.mp4' if work.content_type == 'video' else f'{order}.img')
        if not target.exists():
            download(candidates[0], target, maximum_bytes=maximum-total_bytes)
        total_bytes += target.stat().st_size
        if total_bytes > maximum:
            raise ProcessingError('MEDIA_TOO_LARGE', '整条作品的媒体总量超过配置上限。', False)
        if work.content_type == 'video':
            work.temporary_video = target.relative_to(settings.data_dir).as_posix()
        else:
            try:
                with Image.open(target) as picture:
                    extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(picture.format)
                    if not extension:
                        raise ValueError
                    picture.verify()
            except (ValueError, OSError, UnidentifiedImageError):
                target.unlink(missing_ok=True)
                raise ProcessingError('MEDIA_IMAGE_INVALID', '下载的图片实际格式或完整性无效。', False) from None
            final = root / f'{order}.{extension}'
            target.replace(final)
            add_asset(db, work, final, 'image', order)
