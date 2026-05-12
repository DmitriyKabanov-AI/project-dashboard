#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор README для ProjectDashboard через GigaChat API
Анализирует .py и .html файлы, создаёт компактный README
Особое внимание - функциям apply_sync.py (фильтрация по проекту и ID)
"""

import os
import sys
import time
import base64
import requests
import ssl
import warnings
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

warnings.filterwarnings('ignore')
ssl._create_default_https_context = ssl._create_unverified_context

# ===== НАСТРОЙКИ =====
PROJECT_DIR = Path(r"C:\ProjectDashboard")
CLIENT_ID = "019c9f1c-5d79-77a3-9a8a-d48be1597e9f"
CLIENT_SECRET = "19ef83dd-79df-441d-8f54-54c1856e487b"
GIGACHAT_OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_token_cache: Optional[str] = None
_token_time: float = 0


def get_token() -> Optional[str]:
    global _token_cache, _token_time
    if _token_cache and (time.time() - _token_time) < 3600:
        return _token_cache
    
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": "123e4567-e89b-12d3-a456-426614174000",
        "Authorization": f"Basic {auth}"
    }
    data = {"scope": "GIGACHAT_API_PERS"}
    
    try:
        resp = requests.post(GIGACHAT_OAUTH, headers=headers, data=data, verify=False, timeout=30)
        if resp.status_code == 200:
            _token_cache = resp.json()["access_token"]
            _token_time = time.time()
            return _token_cache
    except Exception as e:
        print(f"  ❌ Ошибка токена: {e}")
    return None


def is_excluded_path(file_path: Path) -> bool:
    excluded = {"__pycache__", "venv", ".venv", "env", "site-packages", "dist", "build", ".git", "logs", "sync_exports", "data"}
    if file_path.suffix == '.pyc':
        return True
    return any(ex in file_path.parts for ex in excluded)


def analyze_file_with_gigachat(code: str, filename: str, rel_path: str, file_type: str) -> str:
    token = get_token()
    if not token:
        return "❌ Не удалось получить токен"
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    # Упрощённый промпт для компактного ответа
    if file_type == "html":
        prompt = f"""Кратко проанализируй HTML файл "{filename}" (путь: {rel_path}) из проекта ProjectDashboard.

Ответь в 3-4 строках, укажи:
- Назначение файла
- Основные блоки (максимум 3)
- Какие API вызывает

Код:
```html
{code[:8000]}
```"""
    else:
        # Для apply_sync.py добавляем информацию о фильтрации
        if filename == "apply_sync.py":
            prompt = f"""Кратко проанализируй файл "{filename}" (путь: {rel_path}). Это скрипт синхронизации очереди с MPP.

Обязательно УКАЖИ следующие возможности фильтрации:
- --project <имя_проекта> - обработать заявки только для конкретного проекта
- --id <число> - обработать только одну конкретную заявку по её ID

Ответь в 4-5 строках: назначение, фильтрацию, как обновляет MPP.

Код:
```python
{code[:8000]}
```"""
        else:
            prompt = f"""Кратко проанализируй файл "{filename}" (путь: {rel_path}) из проекта ProjectDashboard.

Ответь в 2-3 строках: назначение, основные функции, ключевые технологии.

Код:
```python
{code[:8000]}
```"""
    
    payload = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    
    try:
        resp = requests.post(GIGACHAT_API, headers=headers, json=payload, verify=False, timeout=120)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        return f"❌ Ошибка API: {resp.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)[:100]}"


def get_all_files(project_dir: Path) -> List[Tuple[Path, str, str]]:
    all_files = []
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in {
            '__pycache__', 'venv', 'env', '.venv', 'logs', 'sync_exports', 'data', '.git'
        }]
        for file in files:
            if file.endswith('.py') and not file.endswith('.pyc'):
                file_path = Path(root) / file
                if not is_excluded_path(file_path):
                    rel_path = file_path.relative_to(project_dir)
                    all_files.append((file_path, str(rel_path), "python"))
            elif file.endswith('.html'):
                file_path = Path(root) / file
                if not is_excluded_path(file_path):
                    rel_path = file_path.relative_to(project_dir)
                    all_files.append((file_path, str(rel_path), "html"))
    return all_files


def main():
    print("=" * 60)
    print("🚀 Генерация компактного README через GigaChat")
    print("=" * 60)
    print(f"📁 Папка: {PROJECT_DIR}\n")
    
    if not PROJECT_DIR.exists():
        print(f"❌ Папка не найдена: {PROJECT_DIR}")
        sys.exit(1)
    
    all_files = get_all_files(PROJECT_DIR)
    py_files = [f for f in all_files if f[2] == "python"]
    html_files = [f for f in all_files if f[2] == "html"]
    
    print(f"📄 Python: {len(py_files)}")
    print(f"🌐 HTML: {len(html_files)}")
    print(f"📦 Всего: {len(all_files)}\n")
    
    results = []
    for idx, (file_path, rel_path, file_type) in enumerate(all_files, 1):
        try:
            code = file_path.read_text(encoding='utf-8', errors='ignore')
            if len(code.strip()) < 150:
                print(f"⏭️ Пропущен (маленький): {rel_path}")
                continue
            
            icon = "🐍" if file_type == "python" else "🌐"
            print(f"[{idx}/{len(all_files)}] {icon} {rel_path}")
            analysis = analyze_file_with_gigachat(code, file_path.name, rel_path, file_type)
            results.append((rel_path, analysis, file_type))
            time.sleep(1.5)
        except Exception as e:
            print(f"❌ Ошибка {rel_path}: {e}")
            results.append((rel_path, f"❌ Ошибка: {e}", file_type))
    
    # Формирование КОМПАКТНОГО README
    readme_lines = [
        "# ProjectDashboard",
        "",
        "Система согласования процентов выполнения задач из MS Project.",
        "",
        f"📅 Анализ: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"📊 Файлов: {len(results)} ({len([r for r in results if r[2]=='python'])} Python, {len([r for r in results if r[2]=='html'])} HTML)",
        "",
        "---",
        "",
        "## 🐍 Python файлы",
        ""
    ]
    
    for rel_path, analysis, file_type in sorted(results):
        if file_type == "python":
            readme_lines.append(f"### `{rel_path}`")
            readme_lines.append("")
            readme_lines.append(analysis.strip())
            readme_lines.append("")
            readme_lines.append("---")
            readme_lines.append("")
    
    readme_lines.append("## 🌐 HTML шаблоны")
    readme_lines.append("")
    
    for rel_path, analysis, file_type in sorted(results):
        if file_type == "html":
            readme_lines.append(f"### `{rel_path}`")
            readme_lines.append("")
            readme_lines.append(analysis.strip())
            readme_lines.append("")
            readme_lines.append("---")
            readme_lines.append("")
    
    # Добавляем блок с примерами использования фильтрации
    readme_lines.extend([
        "## 📌 Фильтрация в apply_sync.py",
        "",
        "Скрипт `scripts/apply_sync.py` поддерживает выборочную обработку заявок:",
        "",
        "```bash",
        "# Обработать все заявки в очереди",
        "python scripts/apply_sync.py",
        "",
        "# Обработать заявки только для конкретного проекта",
        "python scripts/apply_sync.py --project 149.xml",
        "",
        "# Обработать только одну заявку по ID",
        "python scripts/apply_sync.py --id 42",
        "",
        "# Обработать проект по номеру (без .xml)",
        "python scripts/apply_sync.py --project 149",
        "```",
        "",
        "## 📌 Другие полезные скрипты",
        "",
        "| Команда | Назначение |",
        "|---------|------------|",
        "| `python scripts/sync_notifier.py` | Отправка уведомлений |",
        "| `python scripts/show_new_requests.py` | Просмотр новых заявок |",
        "| `python scripts/mark_notified.py --id 123` | Пометка заявки как уведомлённой |",
        "| `python scripts/fill_resources_final.py` | Заполнение ресурсов в БД |",
        "| `python init_db.py` | Инициализация базы данных |",
        ""
    ])
    
    readme_path = PROJECT_DIR / "README.md"
    readme_path.write_text("\n".join(readme_lines), encoding='utf-8')
    
    print("\n" + "=" * 60)
    print(f"✅ README.md сохранён: {readme_path}")
    print(f"📊 Проанализировано: {len(results)} файлов")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ Прервано")
        sys.exit(130)