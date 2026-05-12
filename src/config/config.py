# -*- coding: utf-8 -*-
"""Универсальная конфигурация с относительными путями"""

import os
from pathlib import Path

# Корень проекта (папка, где находится run.py или app.py)
def get_project_root():
    """Возвращает корневую папку проекта"""
    # Ищем маркер проекта (файл .env или папка data)
    current = Path(__file__).resolve().parent.parent.parent
    while current != current.parent:
        if (current / '.env').exists() or (current / 'data').exists():
            return current
        current = current.parent
    # Если не нашли, возвращаем папку с этим файлом
    return Path(__file__).resolve().parent.parent.parent

PROJECT_ROOT = get_project_root()

# Пути относительно корня проекта
DATA_FOLDER = str(PROJECT_ROOT / 'data')
SYNC_QUEUE_FILE = str(PROJECT_ROOT / 'sync_exports' / 'pending_sync.json')
LOG_FOLDER = str(PROJECT_ROOT / 'logs')

# Для обратной совместимости с os.getenv
os.environ.setdefault('DATA_FOLDER', DATA_FOLDER)
os.environ.setdefault('SYNC_QUEUE_FILE', SYNC_QUEUE_FILE)
os.environ.setdefault('LOG_FOLDER', LOG_FOLDER)

def ensure_directories():
    """Создаёт необходимые папки, если их нет"""
    for folder in [DATA_FOLDER, LOG_FOLDER, str(PROJECT_ROOT / 'sync_exports')]:
        Path(folder).mkdir(parents=True, exist_ok=True)

# При импорте создаём папки
ensure_directories()

if __name__ == '__main__':
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"DATA_FOLDER: {DATA_FOLDER}")
    print(f"SYNC_QUEUE_FILE: {SYNC_QUEUE_FILE}")
    print(f"LOG_FOLDER: {LOG_FOLDER}")
