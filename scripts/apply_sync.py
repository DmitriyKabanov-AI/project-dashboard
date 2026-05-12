#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Применение очереди синхронизации к MPP-файлам через COM.
Работает с PostgreSQL.
Поддерживает фильтрацию по проекту (--project) или по ID записи (--id).
"""

import sys
import os

# Добавляем пути для импорта
sys.path.insert(0, r'C:\ProjectDashboard')
sys.path.insert(0, os.path.join(r'C:\ProjectDashboard', 'src'))
sys.path.insert(0, os.path.join(r'C:\ProjectDashboard', 'src', 'config'))
sys.path.insert(0, os.path.join(r'C:\ProjectDashboard', 'src', 'utils'))

import time
import json
import argparse
from datetime import datetime

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
import win32com.client
import pythoncom

import config
from parser import parse_project_xml

# Инициализируем COM
pythoncom.CoInitialize()
load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': os.getenv('DB_NAME', 'dashboard_db'),
    'user': os.getenv('DB_USER', 'dashboard_user'),
    'password': os.getenv('DB_PASSWORD', ''),
}
DATA_FOLDER = config.DATA_FOLDER
SYNC_QUEUE_FILE = config.SYNC_QUEUE_FILE

def db_connect():
    return psycopg2.connect(**DB_CONFIG)

def mark_queue_processed(conn, entry_id):
    cur = conn.cursor()
    cur.execute("UPDATE sync_queue SET status='processed', processed_at=NOW() WHERE id=%s", (entry_id,))
    conn.commit()
    cur.close()

def mark_queue_failed(conn, entry_id, error):
    cur = conn.cursor()
    cur.execute("UPDATE sync_queue SET status='failed', processed_at=NOW() WHERE id=%s", (entry_id,))
    conn.commit()
    cur.close()

def log_to_db(conn, action, project_file, task_uid, task_name, old_percent, new_percent, status='success'):
    cur = conn.cursor()
    details = {'task_uid': task_uid, 'old_percent': old_percent, 'new_percent': new_percent}
    cur.execute("""
        INSERT INTO logs (user_role, username, action, project_file, task_uid, task_name, details, status)
        VALUES ('system','apply_sync',%s,%s,%s,%s,%s,%s)
    """, (action, project_file, task_uid, task_name, Json(details), status))
    conn.commit()
    cur.close()

def log_processed_sync(conn, request_id, task_uid, project_file, text25,
                       old_percent, new_percent, task_name):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO processed_sync
            (request_id, task_uid, project_file, text25, old_percent, new_percent, status, task_name)
        VALUES (%s,%s,%s,%s,%s,%s,'success',%s)
    """, (request_id, task_uid, project_file, text25, old_percent, new_percent, task_name))
    conn.commit()
    cur.close()

def update_percent_in_mpp_by_uid(mpp_path, target_uid, percent_value, max_retries=3):
    result = {'success': False, 'task_name': None, 'task_uid': None, 'old_percent': None, 'error': None}
    if not os.path.exists(mpp_path):
        result['error'] = f"MPP file not found: {mpp_path}"
        return result
    try:
        target_uid_int = int(target_uid)
    except (ValueError, TypeError):
        result['error'] = f"Invalid target UID: '{target_uid}'"
        return result

    for attempt in range(1, max_retries + 1):
        mpp_app = None
        try:
            mpp_app = win32com.client.Dispatch("MSProject.Application")
            try:
                mpp_app.Visible = False
            except:
                pass

            mpp_app.FileOpen(mpp_path)
            found_task = None
            for task in mpp_app.ActiveProject.Tasks:
                if task is None:
                    continue
                if task.UniqueID == target_uid_int:
                    found_task = task
                    break

            if found_task:
                result['task_name'] = found_task.Name
                result['task_uid'] = str(found_task.UniqueID)
                result['old_percent'] = found_task.PercentComplete
                found_task.PercentComplete = percent_value
                result['success'] = True
                mpp_app.FileSave()
                break
            else:
                result['error'] = f"Task with UniqueID={target_uid_int} not found"
                break
        except Exception as e:
            result['error'] = f"COM error (attempt {attempt}): {e}"
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))
            else:
                break
        finally:
            if mpp_app:
                try:
                    mpp_app.FileClose(Save=False)
                except:
                    pass
                try:
                    mpp_app.Quit()
                except:
                    pass
    return result

def main():
    parser = argparse.ArgumentParser(description="Синхронизация процентов из очереди в MPP-файлы.")
    parser.add_argument('-p', '--project', help="Имя XML-файла проекта")
    parser.add_argument('-i', '--id', type=int, help="ID записи в очереди")
    args = parser.parse_args()

    if not os.path.exists(SYNC_QUEUE_FILE):
        print("Файл очереди не найден.")
        return
    with open(SYNC_QUEUE_FILE, 'r', encoding='utf-8') as f:
        all_queue = json.load(f)
    if not all_queue:
        print("Очередь пуста.")
        return

    queue = all_queue
    if args.id:
        queue = [e for e in all_queue if e.get('id') == args.id]
        if not queue:
            print(f"Запись с id={args.id} не найдена.")
            return
        print(f"Обработка одной записи (id={args.id})")
    elif args.project:
        project_filter = args.project if args.project.endswith('.xml') else args.project + '.xml'
        queue = [e for e in all_queue if e.get('project_file') == project_filter]
        if not queue:
            print(f"Записей для проекта '{project_filter}' не найдено.")
            return
        print(f"Обработка {len(queue)} записей для проекта {project_filter}")
    else:
        print(f"Обработка всех записей ({len(queue)})")

    conn = db_connect()
    new_queue = []
    processed_count = 0

    for entry in queue:
        entry_id = entry.get('id')
        print(f"\n--- Обработка ID={entry_id} ---")
        print(f"  Проект: {entry['project_file']}")
        print(f"  UniqueID задачи: {entry.get('task_uid')}")
        print(f"  Новый %: {entry['new_percent']}")

        mpp_file = entry['project_file'].replace('.xml', '.mpp')
        mpp_path = os.path.join(DATA_FOLDER, mpp_file)

        if not os.path.exists(mpp_path):
            print(f"  MPP не найден: {mpp_path}")
            new_queue.append(entry)
            continue

        result = update_percent_in_mpp_by_uid(mpp_path, entry['task_uid'], entry['new_percent'])

        if result['success']:
            print(f"  '{result['task_name']}': {result['old_percent']}% -> {entry['new_percent']}%")
            log_processed_sync(conn, entry.get('request_id'), entry.get('task_uid'),
                               entry['project_file'], entry.get('text25', ''),
                               result['old_percent'], entry['new_percent'], result['task_name'])
            log_to_db(conn, 'apply_sync', entry['project_file'], entry['task_uid'],
                      result['task_name'], result['old_percent'], entry['new_percent'], 'success')
            if entry_id is not None:
                mark_queue_processed(conn, entry_id)
            processed_count += 1
        else:
            print(f"  {result['error']}")
            try:
                log_to_db(conn, 'apply_sync', entry['project_file'], entry['task_uid'],
                          '', None, entry['new_percent'], 'error')
                if entry_id is not None:
                    mark_queue_failed(conn, entry_id, result['error'])
            except Exception as e:
                print(f"  Не удалось записать ошибку в БД: {e}")
            new_queue.append(entry)

    if args.id or args.project:
        remaining = [e for e in all_queue if e not in queue] + new_queue
        final_queue = remaining
    else:
        final_queue = new_queue

    with open(SYNC_QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_queue, f, ensure_ascii=False, indent=2)

    conn.close()
    pythoncom.CoUninitialize()

    print(f"\nУспешно обработано: {processed_count}")
    print(f"Всего записей осталось в очереди: {len(final_queue)}")

if __name__ == '__main__':
    main()