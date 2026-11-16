import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw
from sqlalchemy import select
from .adapters import ProcessingError
from .config import settings
from .models import Asset, Analysis, Knowledge, Embedding


def under(root: Path, path: Path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('文件不在允许的存储目录内')
    return resolved


def free_space():
    space = shutil.disk_usage(settings.data_dir)
    return {'total_bytes': space.total, 'free_bytes': space.free, 'used_bytes': space.used,
            'minimum_free_mb': settings.min_free_space_mb}


def check_space():
    if free_space()['free_bytes'] < settings.min_free_space_mb * 1024 * 1024:
        raise ProcessingError('STORAGE_FULL', '素材盘剩余空间低于安全阈值。', False, severe=True)


def ffmpeg(args):
    if not shutil.which('ffmpeg'):
        raise ProcessingError('FFMPEG_MISSING', '找不到 FFmpeg，声画解析等待安装。', False, severe=True)
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args],
                       check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ProcessingError('MEDIA_PROCESS_FAILED', 'FFmpeg 无法处理媒体，请检查文件完整性。') from None


def add_asset(db, work, path, asset_type, order):
    relative = under(settings.data_dir, path).relative_to(settings.data_dir.resolve()).as_posix()
    existing = db.scalar(select(Asset).where(Asset.work_id == work.id, Asset.asset_type == asset_type, Asset.source_order == order))
    if existing:
        return existing
    row = Asset(work_id=work.id, file_path=relative, asset_type=asset_type, source_order=order,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), byte_size=path.stat().st_size)
    db.add(row)
    db.flush()
    return row


def demo_media(db, work):
    check_space()
    if work.content_type == 'video':
        root = settings.data_dir / 'temporary_video' / work.id
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'original.mp4'
        if not path.exists():
            ffmpeg(['-f', 'lavfi', '-i', 'color=c=0x196574:s=640x360:d=3', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)])
        work.temporary_video = path.relative_to(settings.data_dir).as_posix()
    elif work.content_type == 'image':
        root = settings.data_dir / 'images' / work.id
        root.mkdir(parents=True, exist_ok=True)
        for order in range(2):
            path = root / f'{order}.png'
            if not path.exists():
                picture = Image.new('RGB', (800, 500), (244, 249, 250))
                draw = ImageDraw.Draw(picture)
                draw.rectangle((45, 50, 755, 85), fill=(25, 101, 116))
                draw.text((45, 130), f'MOCK / CREATOR ATLAS\n\nSTEP {order+1}\n\nInput > Evidence > Knowledge', fill=(32, 47, 51))
                picture.save(path)
            add_asset(db, work, path, 'image', order)


def prepare_video(db, work):
    check_space()
    path = under(settings.data_dir / 'temporary_video' / work.id, settings.data_dir / work.temporary_video)
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                                check=True, capture_output=True, timeout=30)
        metadata = json.loads(result.stdout)
        duration = float(metadata['format']['duration'])
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, KeyError):
        raise ProcessingError('MEDIA_PROBE_FAILED', '无法读取视频元信息。') from None
    if duration > 4 * 3600:
        raise ProcessingError('MEDIA_TOO_LONG', '单视频超过 4 小时，需分段后手动导入。', False)
    work.duration_ms = round(duration * 1000)
    audio_dir = settings.data_dir / 'temporary_audio' / work.id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_files = []
    audio_segment_seconds = 480 if settings.ai_provider == 'minimax' and work.source_mode != 'mock' else 600
    if any(stream['codec_type'] == 'audio' for stream in metadata['streams']):
        ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', '16000', '-f', 'segment', '-segment_time', str(audio_segment_seconds), str(audio_dir / 'part-%03d.wav')])
        audio_files = [str(x) for x in sorted(audio_dir.glob('part-*.wav'))]
    frame_dir = settings.data_dir / 'keyframes' / work.id
    frame_dir.mkdir(parents=True, exist_ok=True)
    # 固定间隔与镜头变化共同抽帧；最长 4h / 限制 max_frames，默认均衡上限可配置。
    interval = max(settings.frame_interval, duration / settings.max_frames)
    filter_expression = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})+gt(scene,0.35)',showinfo"
    # showinfo 记录画面实际时间；不将标题当作帧时间。
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'info', '-y', '-i', str(path), '-vf', filter_expression,
               '-fps_mode', 'vfr', '-frames:v', str(settings.max_frames), '-q:v', '3', str(frame_dir / 'frame-%03d.jpg')]
    try:
        result = subprocess.run(command, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ProcessingError('MEDIA_FRAMES_FAILED', '关键帧提取失败。') from None
    import re
    times = [round(float(x) * 1000) for x in re.findall(r'pts_time:([\d.]+)', result.stderr.decode(errors='replace'))]
    frames = []
    for order, frame in enumerate(sorted(frame_dir.glob('frame-*.jpg'))[:settings.max_frames]):
        asset = add_asset(db, work, frame, 'keyframe', order)
        frames.append({'id': asset.id, 'path': str(frame), 'time_ms': times[order] if order < len(times) else 0})
    if frames:
        cover_dir = settings.data_dir / 'covers' / work.id
        cover_dir.mkdir(parents=True, exist_ok=True)
        cover = cover_dir / 'cover.jpg'
        shutil.copy2(frames[0]['path'], cover)
        add_asset(db, work, cover, 'cover', 0)
    return {'duration_ms': work.duration_ms, 'audio_files': audio_files, 'audio_segment_ms': audio_segment_seconds * 1000, 'images': frames,
            'background_audio_note': '保存音轨整体转写；不推测无法识别的背景歌词或音乐名称。'}


def clean_video(db, work):
    if work.processing_state != 'completed' or not work.current_analysis_id:
        raise ValueError('禁止删除尚未完成的原始视频')
    run = db.get(Analysis, work.current_analysis_id)
    item = db.scalar(select(Knowledge).join(Embedding, Embedding.knowledge_item_id == Knowledge.id).where(Knowledge.analysis_id == work.current_analysis_id))
    if not run or run.status != 'success' or not item:
        raise ValueError('解析结果或可检索索引尚未持久化')
    if work.temporary_video:
        path = under(settings.data_dir / 'temporary_video' / work.id, settings.data_dir / work.temporary_video)
        if path.name not in {'original.mp4', 'original.mov', 'original.webm', 'original.mkv'}:
            raise ValueError('禁止清理未核验的视频路径')
        path.unlink(missing_ok=True)
        audio_root = settings.data_dir / 'temporary_audio' / work.id
        if audio_root.exists():
            verified = under(settings.data_dir / 'temporary_audio', audio_root)
            for audio in verified.glob('part-*.wav'):
                under(verified, audio).unlink(missing_ok=True)
        work.temporary_video = None
        db.commit()
