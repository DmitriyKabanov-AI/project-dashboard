import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config import config
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mark_notified.py вЂ“ РїСЂРёРЅСѓРґРёС‚РµР»СЊРЅР°СЏ РїРѕРјРµС‚РєР° Р·Р°СЏРІРѕРє РєР°Рє "СѓРІРµРґРѕРјР»С‘РЅРЅС‹Рµ" (notified=TRUE),
С‡С‚РѕР±С‹ РѕРЅРё Р±РѕР»СЊС€Рµ РЅРµ РѕС‚РїСЂР°РІР»СЏР»РёСЃСЊ. РџРѕРґРґРµСЂР¶РёРІР°РµС‚ С„РёР»СЊС‚СЂС‹.
РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ:
  python mark_notified.py --id 123,456
  python mark_notified.py --source sync_queue --project 205
  python mark_notified.py --controller krylova@triplus.ru --dry-run
"""

import argparse
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT')),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
}

def get_target_entries(conn, args):
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє Р·Р°СЏРІРѕРє, СЃРѕРѕС‚РІРµС‚СЃС‚РІСѓСЋС‰РёС… С„РёР»СЊС‚СЂР°Рј."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if args.source:
        if args.source not in ('sync_queue', 'percent_requests'):
            print(f"вќЊ РќРµРІРµСЂРЅС‹Р№ РёСЃС‚РѕС‡РЅРёРє: {args.source}")
            sys.exit(1)
        tables = [args.source]
    else:
        tables = ['sync_queue', 'percent_requests']

    all_rows = []
    for table in tables:
        sql = f"SELECT id, '{table}' as source, project_file, task_uid, status, notified FROM {table} WHERE status='pending' AND (notified IS FALSE OR notified IS NULL)"
        params = []
        if args.id:
            ids = [int(x.strip()) for x in args.id.split(',')]
            sql += f" AND id = ANY(%s)"
            params.append(ids)
        cur.execute(sql, params if params else None)
        rows = cur.fetchall()
        if args.project:
            rows = [r for r in rows if args.project.lower() in r['project_file'].lower()]
        all_rows.extend(rows)
    cur.close()
    return all_rows

def get_recipients_for_entry(entry, conn):
    """Р’С‹С‡РёСЃР»СЏРµС‚ РїРѕР»СѓС‡Р°С‚РµР»РµР№ Р·Р°СЏРІРєРё (РґР»СЏ С„РёР»СЊС‚СЂР° РїРѕ РєРѕРЅС‚СЂРѕР»Р»С‘СЂСѓ)."""
    from sync_notifier import get_recipients, get_department
    cur = conn.cursor()
    cur.execute(f"SELECT resources FROM {entry['source']} WHERE id=%s", (entry['id'],))
    row = cur.fetchone()
    resources = row[0] if row and row[0] else []
    cur.close()
    fake_entry = {
        'source': entry['source'],
        'project_file': entry['project_file'],
        'task_uid': entry['task_uid'],
        'id': entry['id']
    }
    recipients = get_recipients(conn, fake_entry, resources)
    return recipients

def mark_as_notified(conn, entry, dry_run):
    if dry_run:
        return
    cur = conn.cursor()
    cur.execute(f"UPDATE {entry['source']} SET notified = TRUE WHERE id = %s", (entry['id'],))
    conn.commit()
    cur.close()

def main():
    parser = argparse.ArgumentParser(description='РџРѕРјРµС‚РёС‚СЊ Р·Р°СЏРІРєРё РєР°Рє СѓРІРµРґРѕРјР»С‘РЅРЅС‹Рµ (С‡С‚РѕР±С‹ РЅРµ РѕС‚РїСЂР°РІР»СЏС‚СЊ)')
    parser.add_argument('--id', help='ID Р·Р°СЏРІРєРё РёР»Рё РЅРµСЃРєРѕР»СЊРєРѕ С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ (123,456)')
    parser.add_argument('--source', choices=['sync_queue', 'percent_requests'], help='РСЃС‚РѕС‡РЅРёРє Р·Р°СЏРІРѕРє')
    parser.add_argument('--project', help='Р¤РёР»СЊС‚СЂ РїРѕ РёРјРµРЅРё РїСЂРѕРµРєС‚Р° (СЃРѕРґРµСЂР¶РёС‚ РїРѕРґСЃС‚СЂРѕРєСѓ)')
    parser.add_argument('--controller', help='Р¤РёР»СЊС‚СЂ РїРѕ email РєРѕРЅС‚СЂРѕР»Р»С‘СЂР°')
    parser.add_argument('--dry-run', action='store_true', help='РџРѕРєР°Р·Р°С‚СЊ, С‡С‚Рѕ Р±СѓРґРµС‚ РїРѕРјРµС‡РµРЅРѕ, Р±РµР· РёР·РјРµРЅРµРЅРёР№')
    args = parser.parse_args()

    if not any([args.id, args.source, args.project, args.controller]):
        print("вќЊ РЈРєР°Р¶РёС‚Рµ С…РѕС‚СЏ Р±С‹ РѕРґРёРЅ С„РёР»СЊС‚СЂ: --id, --source, --project, --controller")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"вќЊ РћС€РёР±РєР° Р‘Р”: {e}")
        sys.exit(1)

    entries = get_target_entries(conn, args)

    if args.controller:
        filtered = []
        for e in entries:
            recipients = get_recipients_for_entry(e, conn)
            if args.controller in recipients:
                filtered.append(e)
        entries = filtered

    if not entries:
        print("рџ“­ Р—Р°СЏРІРєРё РїРѕ Р·Р°РґР°РЅРЅС‹Рј С„РёР»СЊС‚СЂР°Рј РЅРµ РЅР°Р№РґРµРЅС‹.")
        conn.close()
        return

    print(f"рџ”Ќ РќР°Р№РґРµРЅРѕ Р·Р°СЏРІРѕРє: {len(entries)}")
    for e in entries:
        print(f"  {e['source']} ID={e['id']} (РїСЂРѕРµРєС‚: {e['project_file']})")

    if args.dry_run:
        print("\nвљ пёЏ Р РµР¶РёРј DRY-RUN: РёР·РјРµРЅРµРЅРёСЏ РЅРµ Р±СѓРґСѓС‚ РїСЂРёРјРµРЅРµРЅС‹.")
    else:
        confirm = input(f"\nвќ“ РџРѕРјРµС‚РёС‚СЊ {len(entries)} Р·Р°СЏРІРѕРє РєР°Рє СѓРІРµРґРѕРјР»С‘РЅРЅС‹Рµ? (y/N): ")
        if confirm.lower() != 'y':
            print("РћС‚РјРµРЅР°.")
            conn.close()
            return
        for e in entries:
            mark_as_notified(conn, e, dry_run=False)
        print(f"вњ… РџРѕРјРµС‡РµРЅРѕ {len(entries)} Р·Р°СЏРІРѕРє.")

    conn.close()

if __name__ == '__main__':
    main()
