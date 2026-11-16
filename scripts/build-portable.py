"""Build a clean Windows x64 ZIP using the project venv and a built frontend."""
import argparse
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parents[1]


def build(stage, ffmpeg):
    def copy(source, target):
        target = stage / target
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', 'site-packages', '*.pth', '__editable__*', 'creator_atlas*.dist-info'))
        else:
            shutil.copy2(source, target)

    base = Path(sys.base_prefix)
    for name in ('python.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll',
                 'vcruntime140_1.dll', 'DLLs', 'Lib', 'LICENSE.txt'):
        copy(base / name, 'python/' + name)
    copy(Path(sys.prefix) / 'Lib/site-packages', 'python/Lib/site-packages')
    for name in ('backend/app', 'backend/alembic', 'backend/alembic.ini', 'frontend/dist',
                 'scripts/manage.py', 'scripts/metis-portable.py', 'fixtures/mock_creators.json', '.env.example'):
        copy(ROOT / name, name)
    for name in ('bin/ffmpeg.exe', 'bin/ffprobe.exe', 'LICENSE', 'README.txt'):
        copy(ffmpeg / name, 'ffmpeg/' + name)
    copy(ROOT / 'docs/METIS_PORTABLE.md', 'README.md')
    (stage / 'Metis.cmd').write_bytes(
        b'@echo off\r\ncd /d "%~dp0"\r\n'
        b'set "PYTHONUTF8=1"\r\nset "PYTHONNOUSERSITE=1"\r\n'
        b'".\\python\\python.exe" -E -s -X utf8 ".\\scripts\\metis-portable.py"\r\n'
        b'if errorlevel 1 pause\r\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', type=Path, required=True, help='FFmpeg build directory containing bin, LICENSE and README.txt')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix:
        parser.error('Build using the project Windows Python 3.12 virtual environment.')
    if not (ROOT / 'frontend/dist/index.html').is_file():
        parser.error('Build the frontend first.')
    output = ROOT / 'dist/Metis-Windows-x64.zip'
    output.parent.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp) / 'Metis'
        build(stage, args.ffmpeg)
        archive = Path(temp) / output.name
        with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=3) as package:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    package.write(path, path.relative_to(stage.parent).as_posix())
        archive.replace(output)
    print(output)


if __name__ == '__main__':
    main()
