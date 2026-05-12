import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config import config
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
show_new_requests.py вЂ“ РІС‹РІРѕРґ СЃРїРёСЃРєР° Р·Р°СЏРІРѕРє, РѕР¶РёРґР°СЋС‰РёС… СѓРІРµРґРѕРјР»РµРЅРёСЏ (notified=FALSE)
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import argparse
from tabulate import tabulate

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT')),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
}

def main():
    parser = argparse.ArgumentParser(description='РџСЂРѕСЃРјРѕС‚СЂ РЅРµРѕС‚РїСЂР°РІР»РµРЅРЅС‹С… Р·Р°СЏРІРѕРє (notified=FALSE)')
    parser.add_argument('--source', choices=['sync_queue', 'percent_requests', 'all'], default='all',
                        help='РСЃС‚РѕС‡РЅРёРє Р·Р°СЏРІРѕРє: sync_queue, percent_requests РёР»Рё all (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ)')
    parser.add_argument('--project', help='Р¤РёР»СЊС‚СЂ РїРѕ РёРјРµРЅРё С„Р°Р№Р»Р° РїСЂРѕРµРєС‚Р° (СЃРѕРґРµСЂР¶РёС‚ РїРѕРґСЃС‚СЂРѕРєСѓ)')
    parser.add_argument('--controller', help='РџРѕРєР°Р·Р°С‚СЊ С‚РѕР»СЊРєРѕ Р·Р°СЏРІРєРё, РєРѕС‚РѕСЂС‹Рµ Р±СѓРґСѓС‚ РѕС‚РїСЂР°РІР»РµРЅС‹ СѓРєР°Р·Р°РЅРЅРѕРјСѓ РєРѕРЅС‚СЂРѕР»Р»С‘СЂСѓ (email)')
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Р‘Р°Р·РѕРІС‹Рµ Р·Р°РїСЂРѕСЃС‹
    queries = []
    if args.source in ('sync_queue', 'all'):
        queries.append(('sync_queue', """
            SELECT id, project_file, task_uid, task_name, resources, created_at
            FROM sync_queue
            WHERE status = 'pending' AND (notified IS FALSE OR notified IS NULL)
        """))
    if args.source in ('percent_requests', 'all'):
        queries.append(('percent_requests', """
            SELECT id, project_file, task_uid, NULL as task_name, resources, timestamp as created_at
            FROM percent_requests
            WHERE status = 'pending' AND (notified IS FALSE OR notified IS NULL)
        """))

    all_rows = []
    for source, sql in queries:
        cur.execute(sql)
        for row in cur:
            row['source'] = source
            # РџСЂРёРјРµРЅСЏРµРј С„РёР»СЊС‚СЂ РїРѕ РїСЂРѕРµРєС‚Сѓ
            if args.project and args.project.lower() not in row['project_file'].lower():
                continue
            all_rows.append(row)

    if not all_rows:
        print("рџ“­ РќРµС‚ Р·Р°СЏРІРѕРє, РѕР¶РёРґР°СЋС‰РёС… СѓРІРµРґРѕРјР»РµРЅРёСЏ.")
        return

    # Р•СЃР»Рё Р·Р°РґР°РЅ С„РёР»СЊС‚СЂ РїРѕ РєРѕРЅС‚СЂРѕР»Р»С‘СЂСѓ, РЅСѓР¶РЅРѕ РґР»СЏ РєР°Р¶РґРѕР№ Р·Р°СЏРІРєРё РІС‹С‡РёСЃР»РёС‚СЊ РїРѕР»СѓС‡Р°С‚РµР»РµР№
    if args.controller:
        from sync_notifier import get_recipients, get_department, DEPT_MAPPING
        filtered = []
        for row in all_rows:
            # РџРѕР»СѓС‡Р°РµРј РѕС‚РґРµР»С‹ РёСЃРїРѕР»РЅРёС‚РµР»РµР№
            resources = row.get('resources')
            if resources and isinstance(resources, list):
                depts = {get_department(r) for r in resources}
            else:
                depts = set()
            # РЎС‚СЂРѕРёРј Р·Р°РїРёСЃСЊ РґР»СЏ get_recipients (РЅСѓР¶РЅС‹ РїРѕР»СЏ source, project_file)
            entry = {
                'source': row['source'],
                'project_file': row['project_file'],
                'task_uid': row['task_uid'],
                'id': row['id']
            }
            recipients = get_recipients(conn, entry, resources if resources else [])
            if args.controller in recipients:
                filtered.append(row)
        all_rows = filtered
        if not all_rows:
            print(f"рџ“­ РќРµС‚ Р·Р°СЏРІРѕРє РґР»СЏ РєРѕРЅС‚СЂРѕР»Р»С‘СЂР° {args.controller}.")
            return

    # РџРѕРґРіРѕС‚РѕРІРєР° РґР°РЅРЅС‹С… РґР»СЏ РІС‹РІРѕРґР°
    table_data = []
    for row in all_rows:
        # РџСЂРµРѕР±СЂР°Р·СѓРµРј СЃРїРёСЃРѕРє СЂРµСЃСѓСЂСЃРѕРІ РІ СЃС‚СЂРѕРєСѓ
        resources_str = ', '.join(row['resources']) if row['resources'] else ''
        # РћР±СЂРµР·Р°РµРј РґР»РёРЅРЅС‹Рµ РЅР°Р·РІР°РЅРёСЏ
        task_name = (row['task_name'] or '')[:60]
        created = row['created_at'].strftime('%Y-%m-%d %H:%M') if row['created_at'] else ''
        table_data.append([
            row['source'],
            row['id'],
            row['project_file'].replace('.xml', ''),
            task_name,
            resources_str,
            created
        ])

    headers = ['РСЃС‚РѕС‡РЅРёРє', 'ID', 'РџСЂРѕРµРєС‚', 'РќР°Р·РІР°РЅРёРµ Р·Р°РґР°С‡Рё', 'РСЃРїРѕР»РЅРёС‚РµР»Рё', 'РЎРѕР·РґР°РЅРѕ']
    print(tabulate(table_data, headers=headers, tablefmt='grid', stralign='left'))
    print(f"\nР’СЃРµРіРѕ Р·Р°СЏРІРѕРє: {len(table_data)}")

    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
