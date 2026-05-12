# -*- coding: utf-8 -*-
"""Логирование в PostgreSQL и файл"""

import os
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

# Загружаем config для получения путей
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'config'))
from config import LOG_FOLDER, PROJECT_ROOT

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME', 'dashboard_db'),
    'user': os.getenv('DB_USER', 'dashboard_user'),
    'password': os.getenv('DB_PASSWORD', ''),
}

def ensure_logs_dir():
    if not os.path.exists(LOG_FOLDER):
        os.makedirs(LOG_FOLDER)

def write_log_file(user_role, action, project_file=None, task_uid=None,
                   task_name=None, details=None, status='success'):
    """Пишет лог в текстовый файл"""
    ensure_logs_dir()
    log_filename = os.path.join(LOG_FOLDER, f'actions_{datetime.now().strftime("%Y-%m-%d")}.log')
    timestamp = datetime.now().isoformat(' ', timespec='seconds')
    
    try:
        details_str = json.dumps(details, ensure_ascii=False) if details else ''
    except:
        details_str = str(details) if details else ''
    
    def escape_field(s):
        if s is None:
            return ''
        return str(s).replace('|', '\\|').replace('\n', ' ').replace('\r', '')
    
    line = (f'{timestamp}|{escape_field(user_role)}|{escape_field(action)}|'
            f'{escape_field(project_file)}|{escape_field(task_uid)}|'
            f'{escape_field(task_name)}|{escape_field(details_str)}|{escape_field(status)}\n')
    
    with open(log_filename, 'a', encoding='utf-8') as f:
        f.write(line)

def log_to_db(user_role, action, project_file=None, task_uid=None,
              task_name=None, details=None, status='success'):
    """Пишет лог в PostgreSQL таблицу logs"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        details_json = None
        if details:
            try:
                if isinstance(details, (dict, list)):
                    details_json = Json(details)
                else:
                    try:
                        parsed = json.loads(details)
                        details_json = Json(parsed)
                    except:
                        details_json = Json({'text': str(details)})
            except:
                details_json = None
        
        cur.execute('''
            INSERT INTO logs (timestamp, user_role, username, action, project_file, task_uid, task_name, details, status)
            VALUES (NOW(), %s, NULL, %s, %s, %s, %s, %s, %s)
        ''', (user_role, action, project_file, task_uid, task_name, details_json, status))
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f'Logger DB error: {e}')

def log_action(user_role, action, project_file=None, task_uid=None,
               task_name=None, details=None, status='success'):
    """Пишет лог и в БД, и в файл"""
    log_to_db(user_role, action, project_file, task_uid, task_name, details, status)
    write_log_file(user_role, action, project_file, task_uid, task_name, details, status)
