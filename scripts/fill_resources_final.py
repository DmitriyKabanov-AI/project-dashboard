import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config import config
# fill_resources_final.py
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from parser import parse_project_xml

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT')),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
}
DATA_FOLDER = os.getenv('DATA_FOLDER', r'C:\Test\data')

def find_xml_by_project_number(project_number):
    for f in os.listdir(DATA_FOLDER):
        if f.lower().endswith('.xml') and re.search(rf'(?:^|_)({re.escape(project_number)})(?:_|\.)', f):
            return os.path.join(DATA_FOLDER, f)
    return None

def get_task_resources(project_file, task_uid):
    # РўРѕС‡РЅРѕРµ РёРјСЏ
    xml_path = os.path.join(DATA_FOLDER, project_file)
    if not os.path.exists(xml_path):
        # РџРѕ РЅРѕРјРµСЂСѓ РїСЂРѕРµРєС‚Р°
        match = re.search(r'(\d+)', project_file)
        if match:
            xml_path = find_xml_by_project_number(match.group(1))
            if not xml_path:
                return None
        else:
            return None
    tasks = parse_project_xml(xml_path)
    for t in tasks:
        if t['uid'] == task_uid:
            return t.get('resources', [])
    return None

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=RealDictCursor)

# РЈР±РµРґРёРјСЃСЏ, С‡С‚Рѕ РєРѕР»РѕРЅРєР° resources РµСЃС‚СЊ РІ percent_requests
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='percent_requests' AND column_name='resources'
""")
if not cur.fetchone():
    cur.execute("ALTER TABLE percent_requests ADD COLUMN resources TEXT[]")
    conn.commit()
    print("вћ• Р”РѕР±Р°РІР»РµРЅР° РєРѕР»РѕРЅРєР° resources РІ percent_requests")

# РћР±РЅРѕРІР»СЏРµРј sync_queue
cur.execute("SELECT id, project_file, task_uid FROM sync_queue WHERE status='pending' AND (resources IS NULL OR resources = '{}')")
rows = cur.fetchall()
print(f"sync_queue: РѕР±РЅРѕРІР»СЏРµРј {len(rows)} Р·Р°РїРёСЃРµР№...")
for row in rows:
    res = get_task_resources(row['project_file'], row['task_uid'])
    if res is not None:
        cur.execute("UPDATE sync_queue SET resources = %s WHERE id = %s", (res, row['id']))
        print(f"  вњ“ sync_queue id={row['id']} -> {res}")
    else:
        print(f"  вњ— sync_queue id={row['id']} (РЅРµ СѓРґР°Р»РѕСЃСЊ РЅР°Р№С‚Рё СЂРµСЃСѓСЂСЃС‹)")

# РћР±РЅРѕРІР»СЏРµРј percent_requests
cur.execute("SELECT id, project_file, task_uid FROM percent_requests WHERE status='pending' AND (resources IS NULL OR resources = '{}')")
rows = cur.fetchall()
print(f"\npercent_requests: РѕР±РЅРѕРІР»СЏРµРј {len(rows)} Р·Р°РїРёСЃРµР№...")
for row in rows:
    res = get_task_resources(row['project_file'], row['task_uid'])
    if res is not None:
        cur.execute("UPDATE percent_requests SET resources = %s WHERE id = %s", (res, row['id']))
        print(f"  вњ“ percent_requests id={row['id']} -> {res}")
    else:
        print(f"  вњ— percent_requests id={row['id']} (РЅРµ СѓРґР°Р»РѕСЃСЊ РЅР°Р№С‚Рё СЂРµСЃСѓСЂСЃС‹)")

conn.commit()
cur.close()
conn.close()
print("\nвњ… Р“РѕС‚РѕРІРѕ. РўРµРїРµСЂСЊ СЂРµСЃСѓСЂСЃС‹ СЃРѕС…СЂР°РЅРµРЅС‹.")
