import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config import config
#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import re
import sys
import argparse
import smtplib
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from parser import parse_project_xml

load_dotenv()

# ---------- РљРѕРЅС„РёРіСѓСЂР°С†РёСЏ ----------
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT')),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
}
DATA_FOLDER = os.getenv('DATA_FOLDER', r'C:\Test\data')
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.yandex.ru')
SMTP_PORT = int(os.getenv('SMTP_PORT', 465))
EMAIL_FROM = os.getenv('EMAIL_FROM')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
ALWAYS_NOTIFY = ['sidorova@triplus.ru', 'dkabanov@triplus.ru']

LOG_FILE = 'sync_notifier.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# РњР°РїРїРёРЅРі СЂРµСЃСѓСЂСЃ -> РѕС‚РґРµР» (РїРѕР»РЅС‹Р№, РёР· РІР°С€РµРіРѕ dashboard.html)
DEPT_MAPPING = {
    "Р‘РµР»РѕРІ РњР°РєСЃРёРј (РўРҐ)":"РўРҐ","Р›С‘С€РёРЅР° Р“Р°Р»РёРЅР°":"Р­РЎ","РЎРїРёСЂРѕРІ Р”РјРёС‚СЂРёР№":"РЎРЎРёРђ","Р”РµРЅРёСЃРѕРІ РђР»РµРєСЃРµР№":"РРћ",
    "РЎСѓР±РїРѕРґСЂСЏРґ":"РЎСѓР±РїРѕРґСЂСЏРґ","РќР°Р»РёСѓС…РёРЅ Р”РјРёС‚СЂРёР№":"Р­","РќРµРјС‡РµРЅРєРѕ Р•РіРѕСЂ":"Р“РРџ","РЎР»РѕР±РѕР¶Р°РЅ РђР»РµРєСЃР°РЅРґСЂР°":"РђРЎРћ",
    "РСЃР°РІРЅРёРЅ РњР°РєСЃРёРј":"РћР“Рџ","РљР°С‚РµРЅРёРЅР° РљСЃРµРЅРёСЏ":"РћР“Рџ","РљРѕСЂСЃР°РєРѕРІР° РЎРѕС„СЊСЏ":"РђРЎРћ","Р‘СѓСЂР¶РёРЅСЃРєР°СЏ Р•Р»РµРЅР°":"Р­РЎ",
    "РќР°СЃСЂРµРґРёРЅРѕРІ РЎРµСЂРіРµР№":"РўРҐ","РЁРёР»РѕРІ РљРѕРЅСЃС‚Р°РЅС‚РёРЅ":"РЎРЎРёРђ","РљСЂС‹Р»РѕРІР° РЎРІРµС‚Р»Р°РЅР°":"РўРҐ",
    "РљРѕСЃС‚РёС‡РµРІР° (РЎР°С„РѕРЅРѕРІР°) Р®Р»РёСЏ":"РўРҐ","РЇС‰РµСЂРёС†С‹РЅ РђР»РµРєСЃР°РЅРґСЂ":"РђРЎРћ","РњРѕСЂРѕР·РѕРІ Р’Р°РґРёРј":"РђРЎРћ",
    "Р‘Р»РёРЅРѕРІ Р—Р°С…Р°СЂ":"РђРЎРћ","РҐСЂР°РїРѕРІР° РћР»СЊРіР°":"РРћ","РЎРёРєРёРґРёРЅ РРіРѕСЂСЊ":"РЎРЎРёРђ","Р“РѕРІРѕСЂРєРѕРІ РЎС‚Р°РЅРёСЃР»Р°РІ":"Р“РРџ",
    "Р‘Р°СЂР°Р±Р°РЅРѕРІ РџР°РІРµР»":"РћРЈРџ","РђРЅРґСЂРµРµРІР° РћР»СЊРіР°":"РљР ","Р РѕРјР°С€РѕРІ РђР»РµРєСЃР°РЅРґСЂ":"Р­РЎ","Р‘РµСЂРґС‹С€РµРІР° РњР°СЂРіР°СЂРёС‚Р°":"РРћ",
    "РљРѕС€РєРёРЅ РЎРµСЂРіРµР№":"РЎРЎРёРђ","РРІР°РЅРѕРІР° РћР»СЊРіР°":"РљР ","РўРёС…РѕРЅРѕРІ Р”РµРЅРёСЃ":"РђРЎРћ","РљР°РЅРёРЅР° Р“Р°Р»РёРЅР°":"РђРЎРћ",
    "Р“СЂРёР±РєРѕРІ РђРЅС‚РѕРЅ":"РљР ","Р‘Р°Р№РєРѕРІ Р’Р°РґРёРј":"РЎРЎРёРђ","РЎР°РІРµРЅРєРѕРІР° РђР»Р»Р°":"РўРҐ","Р‘РµР»РѕРІ РњР°РєСЃРёРј":"Р”РµРїР°СЂС‚Р°РјРµРЅС‚ РїСЂРѕРµРєС‚РёСЂРѕРІР°РЅРёСЏ",
    "РџРµР»РµРІРёРЅ РђР»РµРєСЃР°РЅРґСЂ":"Р“РРџ","РњР°РєС€Р°РєРѕРІ РђРЅС‚РѕРЅ":"РћРЈРџ","РЁРєСѓСЂР°С‚ РђР»РµРєСЃР°РЅРґСЂ":"РРћ","РўР°СЂР°СЃРѕРІ РђРЅРґСЂРµР№":"РўРҐ",
    "РљР°СЃСЊСЏРЅРѕРІР° РђРЅРЅР°":"РўРҐ","РљРѕС‚РѕРІ РљРёСЂРёР»Р»":"Р“РРџ","Р‘С‹С‚РѕС‚РѕРІР° РЎРІРµС‚Р»Р°РЅР°":"Р­","Р“СЂРёР±Р°РЅРѕРІР° РЇРЅР°":"РђРЎРћ",
    "Р”РµРЅРёСЃРѕРІ Р•РІРіРµРЅРёР№":"РРћ","РќРµС‚":"РќРµС‚","РЎСѓС€РєРѕРІ РРІР°РЅ":"Р“РРџ","Р СѓСЃС‚Р°Рј Р–РѕР»РґР°РєР°РµРІ":"Р“РРџ","Р—Р°РєР°Р·С‡РёРє":"Р—Р°РєР°Р·С‡РёРє",
    "РЎРµСЂРіРµРµРІ РњР°РєСЃРёРј":"РљР ","Р“РѕР»СѓР±РµРІ РђРЅРґСЂРµР№":"РљР ","Р›РёС‚РІРёРЅ Р“РµРѕСЂРіРёР№":"РђРЎРћ","Р‘РµР»РѕРІ РќРёРєРёС‚Р°":"РРћ","РџРѕРґСЂСЏРґС‡РёРє":"РџРѕРґСЂСЏРґС‡РёРє",
    "РЇС†РєРѕРІ РўРёРјРѕС„РµР№":"РђРЎРћ","Р”РѕСЂРѕС€РµРЅРєРѕ Р’Р»Р°РґРёРјРёСЂ":"РРћ"
}

def get_department(resource_name):
    return DEPT_MAPPING.get(resource_name, "Р”СЂСѓРіРѕР№")

# ---------- РџРѕРёСЃРє XML ----------
def find_xml_by_project_number(project_number):
    for f in os.listdir(DATA_FOLDER):
        if f.lower().endswith('.xml') and re.search(rf'(?:^|_)({re.escape(project_number)})(?:_|\.)', f):
            return os.path.join(DATA_FOLDER, f)
    return None

def get_task_details(project_file, task_uid):
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃР»РѕРІР°СЂСЊ СЃ name, percent, resources РёР»Рё None."""
    xml_path = os.path.join(DATA_FOLDER, project_file)
    if not os.path.exists(xml_path):
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
            return {
                'name': t.get('name', 'вЂ”'),
                'percent': t.get('percent_complete', 0),
                'resources': t.get('resources', [])
            }
    return None

def get_last_comment(conn, task_uid, project_file):
    cur = conn.cursor()
    cur.execute(
        "SELECT comment_text FROM comments WHERE task_uid=%s AND project_file=%s ORDER BY timestamp DESC LIMIT 1",
        (task_uid, project_file)
    )
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None

def get_recipients(conn, entry, resources):
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ РјРЅРѕР¶РµСЃС‚РІРѕ email-Р°РґСЂРµСЃРѕРІ РїРѕР»СѓС‡Р°С‚РµР»РµР№."""
    recipients = set()
    departments = {get_department(r) for r in resources} if resources else set()
    project_base = entry['project_file'].replace('.xml', '')
    project_number_match = re.search(r'(\d+)', project_base)
    project_number = project_number_match.group(1) if project_number_match else project_base

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT role, email, projects, departments FROM notification_rules")
    rules = cur.fetchall()
    cur.close()

    for rule in rules:
        if rule['role'] == 'gip' and rule['projects']:
            if project_number in rule['projects']:
                recipients.add(rule['email'])
        elif rule['role'] == 'controller' and rule['departments']:
            if departments.intersection(set(rule['departments'])):
                recipients.add(rule['email'])

    recipients.update(ALWAYS_NOTIFY)
    return recipients

def send_email(to_email, entry, task_details, comment):
    project = entry['project_file'].replace('.xml', '')
    task_name = task_details['name'] if task_details else (entry.get('task_name') or 'вЂ”')
    task_uid = entry['task_uid']
    current_percent = f"{task_details['percent']}%" if task_details and task_details['percent'] is not None else (entry.get('current_percent') or 'вЂ”')
    requested = entry.get('requested_percent')
    requested = f"{requested}%" if requested is not None else 'вЂ”'
    approved = entry.get('approved_percent')
    approved = f"{approved}%" if approved is not None else 'вЂ”'
    target = entry.get('target_percent')
    target = f"{target}%" if target is not None else 'вЂ”'
    gip_note = entry.get('gip_note') or 'вЂ”'
    created_at = entry.get('created_at')
    if isinstance(created_at, datetime):
        created_at = created_at.strftime('%Y-%m-%d %H:%M:%S')
    else:
        created_at = str(created_at) if created_at else 'вЂ”'

    source_type = "РѕС‡РµСЂРµРґСЊ СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё" if entry['source'] == 'sync_queue' else "Р·Р°СЏРІРєР° РЅР° СЃРѕРіР»Р°СЃРѕРІР°РЅРёРµ (РІРѕР·РІСЂР°С‚ РѕС‚ Р“РРџР°)"
    comment_text = comment if comment else 'вЂ”'

    plain = f"""
РЈРІР°Р¶Р°РµРјС‹Р№(-Р°СЏ) РєРѕР»Р»РµРіР°!

Р—Р°С„РёРєСЃРёСЂРѕРІР°РЅР° РЅРѕРІР°СЏ Р·Р°СЏРІРєР° РІ {source_type}.

Р”РµС‚Р°Р»Рё Р·Р°СЏРІРєРё:
вЂў РџСЂРѕРµРєС‚: {project}
вЂў Р—Р°РґР°С‡Р°: {task_name} (UID: {task_uid})
вЂў РўРµРєСѓС‰РёР№ % РІС‹РїРѕР»РЅРµРЅРёСЏ: {current_percent}
вЂў Р—Р°РїСЂРѕС€РµРЅРЅС‹Р№ % (РѕС‚ РёСЃРїРѕР»РЅРёС‚РµР»СЏ): {requested}
вЂў РЈС‚РІРµСЂР¶РґС‘РЅРЅС‹Р№ % (РєРѕРЅС‚СЂРѕР»С‘СЂРѕРј): {approved}
вЂў Р¦РµР»РµРІРѕР№ % РґР»СЏ Р·Р°РїРёСЃРё: {target}
вЂў Р—Р°РјРµС‚РєР° Р“РРџР°: {gip_note}
вЂў РљРѕРјРјРµРЅС‚Р°СЂРёР№: {comment_text}
вЂў РЎРѕР·РґР°РЅРѕ: {created_at}

РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РІРѕР№РґРёС‚Рµ РІ СЃРёСЃС‚РµРјСѓ Рё РїСЂРёРјРёС‚Рµ РјРµСЂС‹.
РЎСЃС‹Р»РєР° РЅР° РґР°С€Р±РѕСЂРґ: http://192.168.110.131:8080/

РЎ СѓРІР°Р¶РµРЅРёРµРј,
Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєР°СЏ СЃРёСЃС‚РµРјР° СѓРІРµРґРѕРјР»РµРЅРёР№.
"""
    html = plain.replace('\n', '<br>')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"[РћС‡РµСЂРµРґСЊ СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё] РќРѕРІР°СЏ Р·Р°СЏРІРєР° РїРѕ РїСЂРѕРµРєС‚Сѓ {project}"
    msg['From'] = EMAIL_FROM
    msg['To'] = to_email
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        logger.info(f"РџРёСЃСЊРјРѕ РѕС‚РїСЂР°РІР»РµРЅРѕ РґР»СЏ {entry['source']} ID={entry['id']} -> {to_email}")
        return True
    except Exception as e:
        logger.error(f"РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё РЅР° {to_email}: {e}")
        return False

def get_pending_entries(conn, project_filter=None, controller_filter=None):
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє Р·Р°СЏРІРѕРє, РєРѕС‚РѕСЂС‹Рµ РЅСѓР¶РЅРѕ РѕС‚РїСЂР°РІРёС‚СЊ, СЃ СѓС‡С‘С‚РѕРј С„РёР»СЊС‚СЂРѕРІ."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # sync_queue
    sql_sync = """
        SELECT id, 'sync_queue' as source, task_uid, project_file,
               new_percent as target_percent, requested_percent, approved_percent,
               current_percent, gip_note, created_at, task_name, resources
        FROM sync_queue
        WHERE status = 'pending' AND (notified IS NULL OR notified = FALSE)
    """
    if project_filter:
        sql_sync += " AND project_file LIKE %s"
    cur.execute(sql_sync, (f'%{project_filter}%',) if project_filter else None)
    sync_rows = cur.fetchall()
    # percent_requests
    sql_pr = """
        SELECT pr.id, 'percent_requests' as source, pr.task_uid, pr.project_file,
               pr.requested_percent as target_percent, pr.requested_percent,
               NULL as approved_percent, NULL as current_percent, '' as gip_note,
               pr.timestamp as created_at, NULL as task_name, pr.resources
        FROM percent_requests pr
        LEFT JOIN sync_queue sq ON sq.request_id = pr.id AND sq.status != 'returned'
        WHERE pr.status = 'pending' AND (pr.notified IS NULL OR pr.notified = FALSE)
          AND sq.id IS NULL
    """
    if project_filter:
        sql_pr += " AND pr.project_file LIKE %s"
    cur.execute(sql_pr, (f'%{project_filter}%',) if project_filter else None)
    pr_rows = cur.fetchall()
    cur.close()
    all_entries = sync_rows + pr_rows

    if controller_filter:
        # Р¤РёР»СЊС‚СЂР°С†РёСЏ РїРѕ РїРѕР»СѓС‡Р°С‚РµР»СЏРј
        filtered = []
        for entry in all_entries:
            # РџРѕР»СѓС‡Р°РµРј СЂРµСЃСѓСЂСЃС‹: СЃРЅР°С‡Р°Р»Р° РёР· Р‘Р”, РµСЃР»Рё РЅРµС‚ вЂ“ РёР· XML
            resources = entry.get('resources')
            if not resources and entry['source'] == 'percent_requests':
                # РЈ percent_requests РјРѕРіР»Рё РЅРµ Р·Р°РїРѕР»РЅРёС‚СЊ resources, РїРѕРїСЂРѕР±СѓРµРј РёР· XML
                details = get_task_details(entry['project_file'], entry['task_uid'])
                if details:
                    resources = details['resources']
            if not resources:
                # РўР°РєР¶Рµ РІРѕР·РјРѕР¶РЅРѕ, С‡С‚Рѕ resources РІ Р‘Р” РµСЃС‚СЊ, РЅРѕ РїСѓСЃС‚РѕР№ РјР°СЃСЃРёРІ
                resources = []
            recipients = get_recipients(conn, entry, resources)
            if controller_filter in recipients:
                filtered.append(entry)
        logger.info(f"Р¤РёР»СЊС‚СЂ РїРѕ РєРѕРЅС‚СЂРѕР»Р»С‘СЂСѓ {controller_filter}: РЅР°Р№РґРµРЅРѕ {len(filtered)} Р·Р°СЏРІРѕРє.")
        return filtered
    return all_entries

def mark_notified(conn, source, record_id):
    cur = conn.cursor()
    if source == 'sync_queue':
        cur.execute("UPDATE sync_queue SET notified = TRUE WHERE id = %s", (record_id,))
    else:
        cur.execute("UPDATE percent_requests SET notified = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', help='Р¤РёР»СЊС‚СЂ РїРѕ РЅРѕРјРµСЂСѓ РїСЂРѕРµРєС‚Р° (Р±СѓРґРµС‚ РёСЃРєР°С‚СЊ РІС…РѕР¶РґРµРЅРёРµ РІ project_file)')
    parser.add_argument('--controller', help='Р¤РёР»СЊС‚СЂ РїРѕ email РєРѕРЅС‚СЂРѕР»Р»С‘СЂР°')
    args = parser.parse_args()

    logger.info("=== Р—Р°РїСѓСЃРє sync_notifier.py ===")
    if args.project:
        logger.info(f"Р¤РёР»СЊС‚СЂ РїРѕ РїСЂРѕРµРєС‚Сѓ: {args.project}")
    if args.controller:
        logger.info(f"Р¤РёР»СЊС‚СЂ РїРѕ РєРѕРЅС‚СЂРѕР»Р»С‘СЂСѓ: {args.controller}")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє Р‘Р”: {e}")
        sys.exit(1)

    try:
        entries = get_pending_entries(conn, args.project, args.controller)
        if not entries:
            logger.info("РќРµС‚ Р·Р°СЏРІРѕРє РґР»СЏ РѕС‚РїСЂР°РІРєРё.")
            return
        logger.info(f"РќР°Р№РґРµРЅРѕ {len(entries)} Р·Р°СЏРІРѕРє.")

        for entry in entries:
            # РџРѕР»СѓС‡Р°РµРј Р°РєС‚СѓР°Р»СЊРЅС‹Рµ РґР°РЅРЅС‹Рµ РёР· XML (РёРјСЏ, РїСЂРѕС†РµРЅС‚, СЂРµСЃСѓСЂСЃС‹) РґР»СЏ С‚РµР»Р° РїРёСЃСЊРјР°
            task_details = get_task_details(entry['project_file'], entry['task_uid'])
            resources = task_details['resources'] if task_details else entry.get('resources')
            if resources and entry['source'] == 'sync_queue' and not entry.get('resources'):
                # РЎРѕС…СЂР°РЅРёРј РІ Р‘Р” РґР»СЏ Р±СѓРґСѓС‰РёС… Р·Р°РїСѓСЃРєРѕРІ
                cur = conn.cursor()
                cur.execute("UPDATE sync_queue SET resources = %s WHERE id = %s", (resources, entry['id']))
                conn.commit()
                cur.close()
            comment = get_last_comment(conn, entry['task_uid'], entry['project_file'])
            recipients = get_recipients(conn, entry, resources if resources else [])
            if not recipients:
                logger.info(f"Р—Р°СЏРІРєР° {entry['source']} ID={entry['id']}: РЅРµС‚ РїРѕР»СѓС‡Р°С‚РµР»РµР№, РїСЂРѕРїСѓСЃРє.")
                continue
            logger.info(f"Р—Р°СЏРІРєР° ID={entry['id']}: РїРѕР»СѓС‡Р°С‚РµР»Рё: {', '.join(recipients)}")
            success = False
            for email in recipients:
                if send_email(email, entry, task_details, comment):
                    success = True
                time.sleep(2)  # РїР°СѓР·Р° РјРµР¶РґСѓ РїРёСЃСЊРјР°РјРё
            if success:
                mark_notified(conn, entry['source'], entry['id'])
                logger.info(f"Р—Р°СЏРІРєР° {entry['source']} ID={entry['id']} РїРѕРјРµС‡РµРЅР° РєР°Рє СѓРІРµРґРѕРјР»С‘РЅРЅР°СЏ.")
            else:
                logger.warning(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ РЅРё РѕРґРЅРѕРіРѕ РїРёСЃСЊРјР° РґР»СЏ Р·Р°СЏРІРєРё ID={entry['id']}")
            time.sleep(1)  # РїР°СѓР·Р° РјРµР¶РґСѓ Р·Р°СЏРІРєР°РјРё
    except Exception as e:
        logger.exception(f"РљСЂРёС‚РёС‡РµСЃРєР°СЏ РѕС€РёР±РєР°: {e}")
    finally:
        conn.close()
        logger.info("Р—Р°РІРµСЂС€РµРЅРёРµ СЂР°Р±РѕС‚С‹.")

if __name__ == '__main__':
    main()
