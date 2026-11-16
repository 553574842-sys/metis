"""Start the self-contained Windows build on the local computer."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parents[1]


def open_when_ready(url):
    for _ in range(100):
        try:
            with urlopen(url + '/health', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, URLError):
            time.sleep(0.3)


def serve():
    os.environ['PATH'] = str(ROOT / 'ffmpeg' / 'bin') + os.pathsep + os.environ['PATH']
    sys.path.insert(0, str(ROOT / 'backend'))

    # Seed only an empty installation. Keep migration and seed processes isolated
    # so settings created during initialization are read afresh on startup.
    subprocess.run([sys.executable, 'scripts/manage.py', 'init'], check=True)
    from app.config import settings
    from app.db import Session
    from app.models import Creator
    from sqlalchemy import select
    if not (ROOT / '.metis-started').exists():
        with Session() as db:
            empty = db.scalar(select(Creator.id).limit(1)) is None
        if empty and settings.ai_provider == settings.adapter_douyin_mode == settings.adapter_xhs_mode == 'mock':
            subprocess.run([sys.executable, 'scripts/manage.py', 'demo'], check=True)
        (ROOT / '.metis-started').touch()

    import uvicorn
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        settings.app_base_url = url
        settings.allowed_origins = url
        (ROOT / 'runtime/portable-url.txt').write_text(url, encoding='ascii')
        print(f'Metis  {url}', flush=True)
        Thread(target=open_when_ready, args=(url,), daemon=True).start()
        server = uvicorn.Server(uvicorn.Config('app.main:app', access_log=False, proxy_headers=False))
        server.run(sockets=[listener])


def main():
    from filelock import FileLock, Timeout

    os.chdir(ROOT)
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    try:
        with FileLock(runtime / 'portable.lock', timeout=0):
            address = runtime / 'portable-url.txt'
            address.unlink(missing_ok=True)
            serve()
    except Timeout:
        address = runtime / 'portable-url.txt'
        if address.is_file():
            webbrowser.open(address.read_text(encoding='ascii'))
        else:
            print('Metis 正在启动，请稍候。')


if __name__ == '__main__':
    main()
