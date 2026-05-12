#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Инициализация базы данных PostgreSQL для Project Dashboard.

Запуск: python init_db.py
"""

import os
import sys
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import bcrypt

# Загружаем .env
load_dotenv()

# Конфигурация подключения к админской БД postgres
ADMIN_DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'database': 'postgres',
    'user': 'postgres',
    'password': os.getenv('DB_PASSWORD', 'StrongPass123'),
}

# Параметры создаваемой БД и пользователя
TARGET_DB_NAME = os.getenv('DB_NAME', 'dashboard_db')
TARGET_DB_USER = os.getenv('DB_USER', 'dashboard_user')
TARGET_DB_PASSWORD = os.getenv('DB_PASSWORD', 'StrongPass123')


def create_database_and_user():
    """Создаёт БД и пользователя, если их нет"""
    try:
        conn = psycopg2.connect(**ADMIN_DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()

        # Создаём пользователя
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (TARGET_DB_USER,))
        if not cur.fetchone():
            print(f"👤 Создаём пользователя {TARGET_DB_USER}...")
            cur.execute(sql.SQL("CREATE USER {} WITH PASSWORD %s").format(
                sql.Identifier(TARGET_DB_USER)), (TARGET_DB_PASSWORD,))
            print(f"✅ Пользователь {TARGET_DB_USER} создан")
        else:
            print(f"✅ Пользователь {TARGET_DB_USER} уже существует")

        # Создаём базу данных
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TARGET_DB_NAME,))
        if not cur.fetchone():
            print(f"🗄️ Создаём базу данных {TARGET_DB_NAME}...")
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(TARGET_DB_NAME),
                sql.Identifier(TARGET_DB_USER)
            ))
            print(f"✅ База данных {TARGET_DB_NAME} создана")
        else:
            print(f"✅ База данных {TARGET_DB_NAME} уже существует")

        cur.close()
        conn.close()
        return True

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


def create_tables():
    """Создаёт все таблицы"""
    try:
        conn = psycopg2.connect(
            host=ADMIN_DB_CONFIG['host'],
            port=ADMIN_DB_CONFIG['port'],
            database=TARGET_DB_NAME,
            user=TARGET_DB_USER,
            password=TARGET_DB_PASSWORD
        )
        conn.autocommit = True
        cur = conn.cursor()

        print("📝 Создаём таблицы...")

        tables_sql = """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('executor','controller','gip')),
            is_active BOOLEAN DEFAULT TRUE,
            last_login TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            task_uid TEXT NOT NULL,
            project_file TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id),
            user_role TEXT,
            username TEXT,
            comment_text TEXT,
            timestamp TIMESTAMP DEFAULT NOW(),
            task_name TEXT
        );

        CREATE TABLE IF NOT EXISTS percent_requests (
            id SERIAL PRIMARY KEY,
            task_uid TEXT NOT NULL,
            project_file TEXT NOT NULL,
            requested_percent INTEGER,
            approved_percent INTEGER,
            status TEXT DEFAULT 'pending',
            created_by TEXT,
            reviewed_by TEXT,
            timestamp TIMESTAMP DEFAULT NOW(),
            reviewed_at TIMESTAMP,
            resources TEXT[],
            notified BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS sync_queue (
            id SERIAL PRIMARY KEY,
            request_id INTEGER,
            task_uid TEXT NOT NULL,
            project_file TEXT NOT NULL,
            text25 TEXT,
            new_percent INTEGER,
            requested_percent INTEGER,
            approved_percent INTEGER,
            current_percent INTEGER,
            gip_note TEXT,
            status TEXT DEFAULT 'pending',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP,
            task_name TEXT,
            resources TEXT[],
            notified BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS processed_sync (
            id SERIAL PRIMARY KEY,
            request_id INTEGER,
            task_uid TEXT,
            project_file TEXT,
            text25 TEXT,
            old_percent INTEGER,
            new_percent INTEGER,
            processed_at TIMESTAMP DEFAULT NOW(),
            status TEXT,
            task_name TEXT
        );

        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT NOW(),
            user_role TEXT,
            username TEXT,
            action TEXT,
            project_file TEXT,
            task_uid TEXT,
            task_name TEXT,
            details JSONB,
            status TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT NOW(),
            user_id INTEGER,
            username TEXT,
            user_role TEXT,
            action TEXT,
            target TEXT,
            ip_address TEXT,
            details JSONB,
            status TEXT
        );

        CREATE TABLE IF NOT EXISTS task_percent_history (
            id SERIAL PRIMARY KEY,
            task_uid TEXT,
            project_file TEXT,
            old_percent INTEGER,
            new_percent INTEGER,
            changed_by TEXT,
            changed_at TIMESTAMP DEFAULT NOW(),
            source TEXT
        );

        CREATE TABLE IF NOT EXISTS notification_rules (
            id SERIAL PRIMARY KEY,
            role TEXT NOT NULL,
            email TEXT NOT NULL,
            projects TEXT[],
            departments TEXT[]
        );

        CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue(status);
        CREATE INDEX IF NOT EXISTS idx_sync_queue_notified ON sync_queue(notified);
        CREATE INDEX IF NOT EXISTS idx_percent_requests_status ON percent_requests(status);
        CREATE INDEX IF NOT EXISTS idx_percent_requests_notified ON percent_requests(notified);
        CREATE INDEX IF NOT EXISTS idx_comments_task ON comments(task_uid, project_file);
        """

        for statement in tables_sql.split(';'):
            if statement.strip():
                try:
                    cur.execute(statement)
                except Exception as e:
                    print(f"  ⚠️ {e}")

        cur.close()
        conn.close()
        print("✅ Таблицы созданы")
        return True

    except Exception as e:
        print(f"❌ Ошибка при создании таблиц: {e}")
        return False


def create_admin_user():
    """Создаёт тестового пользователя admin / admin123"""
    try:
        conn = psycopg2.connect(
            host=ADMIN_DB_CONFIG['host'],
            port=ADMIN_DB_CONFIG['port'],
            database=TARGET_DB_NAME,
            user=TARGET_DB_USER,
            password=TARGET_DB_PASSWORD
        )
        cur = conn.cursor()

        pw_hash = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        cur.execute("""
            INSERT INTO users (username, password_hash, role, is_active)
            VALUES ('admin', %s, 'gip', TRUE)
            ON CONFLICT (username) DO NOTHING
        """, (pw_hash,))

        conn.commit()
        cur.close()
        conn.close()
        print("✅ Пользователь admin / admin123 создан")
        return True

    except Exception as e:
        print(f"⚠️ Не удалось создать admin: {e}")
        return False


def main():
    print("=" * 60)
    print("Инициализация базы данных Project Dashboard")
    print("=" * 60)

    if not os.path.exists('.env'):
        print("❌ Файл .env не найден!")
        print("   Скопируйте .env.example в .env и заполните DB_PASSWORD")
        sys.exit(1)

    print("\n1️⃣ Создание БД и пользователя...")
    if not create_database_and_user():
        sys.exit(1)

    print("\n2️⃣ Создание таблиц...")
    if not create_tables():
        sys.exit(1)

    print("\n3️⃣ Создание администратора...")
    create_admin_user()

    print("\n" + "=" * 60)
    print("✅ Готово!")
    print("\nЗапуск: python app.py")
    print("Логин: admin")
    print("Пароль: admin123")
    print("=" * 60)


if __name__ == '__main__':
    main()