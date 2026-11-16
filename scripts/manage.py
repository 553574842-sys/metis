"""Local operations. Use project .venv; no real platform/cloud requests in demo."""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def init_env():
    example = ROOT / '.env.example'
    target = ROOT / '.env'
    if not target.exists():
        value = example.read_text(encoding='utf-8').replace('ADMIN_PASSWORD=CHANGE_ME_USE_INIT_SCRIPT','ADMIN_PASSWORD='+secrets.token_urlsafe(24)).replace(
            'POSTGRES_PASSWORD=CHANGE_ME_USE_INIT_SCRIPT','POSTGRES_PASSWORD='+secrets.token_urlsafe(24))
        target.write_text(value,encoding='utf-8')
    local = ROOT / '.env.local'
    if not local.exists():
        local.write_text('DATABASE_URL=sqlite:///'+(ROOT/'data'/'atlas.db').as_posix()+'\nTASK_MODE=local\nDATA_DIR='+
                         (ROOT/'data').as_posix()+'\nBACKUP_DIR='+(ROOT/'backups').as_posix()+'\nLOCAL_PASSWORDLESS=true\n',encoding='utf-8')
    print('本机配置已就绪。管理员密码保存在 .env，不写入日志。')


def migrate():
    from alembic.config import Config
    from alembic import command
    config = Config(str(ROOT/'backend'/'alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'backend'/'alembic'))
    command.upgrade(config,'head')
    from app.db import Session
    from app.security import initialize_admin
    with Session() as db:
        initialize_admin(db)
    print('数据库迁移完成，管理员已初始化。')


def demo():
    from app.config import settings
    if settings.ai_provider != 'mock' or settings.adapter_douyin_mode != 'mock' or settings.adapter_xhs_mode != 'mock':
        raise SystemExit('演示命令只允许纯 Mock 配置。')
    from app.db import Session
    from app.main import add_creator
    from app.schemas import CreatorInput
    from app.jobs import run_until_idle
    from app.knowledge import daily_report
    from datetime import datetime
    from zoneinfo import ZoneInfo
    creators = json.loads((ROOT/'fixtures'/'mock_creators.json').read_text(encoding='utf-8'))
    with Session() as db:
        for creator in creators:
            add_creator(db,CreatorInput(**creator))
    processed = run_until_idle()
    with Session() as db:
        report = daily_report(db,datetime.now(ZoneInfo(settings.app_timezone)).date().isoformat())
    print(json.dumps({'mode':'mock','processed_jobs':processed,'report_date':report.report_date,'report_status':report.status},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['init','migrate','demo','backup','restore','openapi','prepare-embeddings','capture-link','brain-sync','night-batch'])
    parser.add_argument('--url')
    parser.add_argument('--creator-id')
    parser.add_argument('--max-videos', type=int, default=10)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--target',type=Path)
    parser.add_argument('--database-url')
    args = parser.parse_args()
    if args.command=='init':
        init_env(); migrate()
    elif args.command=='migrate':
        migrate()
    elif args.command=='demo':
        demo()
    elif args.command=='backup':
        from app.backup import create_backup
        print(json.dumps(create_backup(),ensure_ascii=False))
    elif args.command=='restore':
        if not all([args.source,args.target,args.database_url]):
            parser.error('restore 需要 --source --target --database-url；目标必须是空库与新目录')
        from app.backup import restore_backup
        print(json.dumps(restore_backup(args.source,args.target,args.database_url),ensure_ascii=False))
    elif args.command=='openapi':
        from app.main import app
        (ROOT/'docs'/'openapi.json').write_text(json.dumps(app.openapi(),ensure_ascii=False,indent=2),encoding='utf-8')
        print('docs/openapi.json 已生成，不含凭据。')
    elif args.command=='prepare-embeddings':
        from app.local_embeddings import prepare_model
        print(json.dumps(prepare_model(), ensure_ascii=False))
    elif args.command=='capture-link':
        if not args.url:
            parser.error('capture-link 需要 --url 抖音链接或分享文案')
        from app.db import Session
        from app.pipeline import enqueue_link_capture
        with Session() as db:
            print(json.dumps(enqueue_link_capture(db, args.url), ensure_ascii=False))
    elif args.command=='brain-sync':
        from app.db import Session
        from app.brain_sync import enqueue_all
        with Session() as db:
            print(json.dumps(enqueue_all(db), ensure_ascii=False))
    elif args.command=='night-batch':
        if not args.creator_id:
            parser.error('night-batch 需要 --creator-id')
        from app.db import Session
        from app.models import Creator
        from app.night_batch import create, summary
        with Session() as db:
            creator = db.get(Creator, args.creator_id)
            if not creator:
                parser.error('账号不存在')
            print(json.dumps(summary(db, create(db, creator, args.max_videos)), ensure_ascii=True))


if __name__=='__main__':
    main()
