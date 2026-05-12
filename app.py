import sys
import os
import atexit

# Добавляем пути для импорта модулей
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'utils'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'config'))

# -*- coding: utf-8 -*-
"""
Production-ready приложение согласования процентов MS Project.
PostgreSQL + Flask-Login + bcrypt + Prometheus + Waitress.
"""
import json
import time
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify, session,
    redirect, url_for, flash, Response
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
import bcrypt
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from prometheus_client import (
    Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
)
from waitress import serve
import structlog

from parser import parse_project_xml
from logger import log_action
import config

# ------------------------------------------------------------
# Конфигурация
# ------------------------------------------------------------
load_dotenv()

DB_CONFIG = {
    'host': str(os.getenv('DB_HOST', 'localhost')),
    'port': int(str(os.getenv('DB_PORT', 5432))),
    'database': str(os.getenv('DB_NAME', 'dashboard_db')),
    'user': str(os.getenv('DB_USER', 'dashboard_user')),
    'password': str(os.getenv('DB_PASSWORD', '')),
    'client_encoding': 'UTF8',
}

SECRET_KEY          = os.getenv('SECRET_KEY', 'change-me-in-production')
SESSION_TTL         = int(os.getenv('SESSION_TTL', 28800))
ENABLE_REGISTRATION = os.getenv('ENABLE_REGISTRATION', 'True').lower() in ('true', '1', 'yes')
DATA_FOLDER         = config.DATA_FOLDER
SYNC_QUEUE_FILE     = config.SYNC_QUEUE_FILE
LOG_FOLDER          = config.LOG_FOLDER
LOG_LEVEL           = os.getenv('LOG_LEVEL', 'INFO')
PORT                = int(os.getenv('PORT', 8080))

# structlog
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.JSONRenderer(),
    ]
)
slog = structlog.get_logger()

# ------------------------------------------------------------
# Flask init
# ------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(seconds=SESSION_TTL)

# ------------------------------------------------------------
# Пул соединений (исправлен)
# ------------------------------------------------------------
_db_pool = None

def get_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = SimpleConnectionPool(5, 20, **DB_CONFIG)
    return _db_pool

def get_db():
    return get_pool().getconn()

def put_db(conn):
    if conn:
        try:
            get_pool().putconn(conn)
        except Exception:
            pass

# Закрываем все соединения при выходе
def close_db_pool():
    global _db_pool
    if _db_pool:
        _db_pool.closeall()
        _db_pool = None

atexit.register(close_db_pool)

# ------------------------------------------------------------
# Lock для JSON-очереди
# ------------------------------------------------------------
queue_lock = threading.Lock()

# ------------------------------------------------------------
# Prometheus-метрики
# ------------------------------------------------------------
REQUEST_COUNT     = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY   = Histogram('http_request_duration_seconds', 'HTTP request duration', ['endpoint'])
QUEUE_SIZE_GAUGE  = Gauge('sync_queue_pending', 'Pending entries in sync queue')
PROCESSED_COUNT   = Counter('sync_processed_total', 'Total processed sync entries')
LOGIN_COUNT       = Counter('login_attempts_total', 'Login attempts', ['status'])
ACTIVE_USERS      = Gauge('active_users', 'Active users in last hour')

# ------------------------------------------------------------
# Flask-Login
# ------------------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Необходимо войти в систему.'

class User(UserMixin):
    def __init__(self, id, username, role, is_active=True):
        self.id = id
        self.username = username
        self.role = role
        self._is_active = is_active

    @property
    def is_active(self):
        return self._is_active

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, username, role, is_active FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            return User(row['id'], row['username'], row['role'], row['is_active'])
    finally:
        put_db(conn)
    return None

# ------------------------------------------------------------
# Декораторы
# ------------------------------------------------------------
def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({'error': 'Unauthorized'}), 401
            if current_user.role not in roles:
                return jsonify({'error': 'Access denied'}), 403
            return f(*args, **kwargs)
        return wrapper
    return deco

# ------------------------------------------------------------
# Аудит (исправлен)
# ------------------------------------------------------------
def audit(action, target=None, details=None, status='success'):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        user_id = current_user.id if current_user.is_authenticated else None
        username = current_user.username if current_user.is_authenticated else None
        role = current_user.role if current_user.is_authenticated else None
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        details_json = Json(details) if details else None
        cur.execute("""
            INSERT INTO audit_log (user_id, username, user_role, action, target, ip_address, details, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (user_id, username, role, action, target, ip, details_json, status))
        conn.commit()
        cur.close()
    except Exception as e:
        slog.error("audit_error", error=str(e))
    finally:
        if conn:
            put_db(conn)

# ------------------------------------------------------------
# Хелперы
# ------------------------------------------------------------
def get_task_current_percent(project_file, task_uid):
    xml_path = os.path.join(DATA_FOLDER, project_file)
    if not os.path.exists(xml_path):
        return None
    tasks = parse_project_xml(xml_path)
    for t in tasks:
        if t['uid'] == task_uid:
            return int(t.get('percent_complete') or 0)
    return None

def build_task_tree(flat_tasks):
    tasks = sorted(flat_tasks, key=lambda t: int(t.get('id', 0) or 0))
    if tasks and int(tasks[0].get('outline_level', 0)) == 0:
        tasks = tasks[1:]

    root_children = []
    stack = []

    for task in tasks:
        level = int(task.get('outline_level', 1) or 1)
        node = dict(task)
        node['children'] = []

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            parent_node = stack[-1][1]
            parent_node['children'].append(node)
            parent_node['summary'] = True
        else:
            root_children.append(node)

        stack.append((level, node))

    return root_children

# ------------------------------------------------------------
# JSON-очередь
# ------------------------------------------------------------
def _row_to_entry(row):
    e = dict(row)
    for k in ('created_at', 'processed_at'):
        if e.get(k):
            e[k] = e[k].isoformat() if hasattr(e[k], 'isoformat') else str(e[k])
    e['task_name'] = row.get('task_name')
    return e

def read_json_queue():
    with queue_lock:
        if not os.path.exists(SYNC_QUEUE_FILE):
            return []
        try:
            with open(SYNC_QUEUE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

def write_json_queue(queue):
    with queue_lock:
        os.makedirs(os.path.dirname(SYNC_QUEUE_FILE), exist_ok=True)
        with open(SYNC_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2, default=str)

def sync_queue_to_json():
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, request_id, task_uid, project_file, text25, new_percent,
                   requested_percent, approved_percent, current_percent,
                   gip_note, status, created_at, task_name
            FROM sync_queue
            WHERE status = 'pending'
            ORDER BY id
        """)
        rows = cur.fetchall()
        cur.close()
        queue = [_row_to_entry(r) for r in rows]
        write_json_queue(queue)
        QUEUE_SIZE_GAUGE.set(len(queue))
        slog.info("queue_synced", count=len(queue))
    except Exception as e:
        slog.error("sync_queue_to_json_error", error=str(e))
    finally:
        put_db(conn)

def queue_insert(request_id, task_uid, project_file, text25, new_percent,
                 requested_percent=None, approved_percent=None,
                 current_percent=None, gip_note='', created_by=None,
                 task_name=None):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT gip_note, approved_percent, new_percent
            FROM sync_queue
            WHERE task_uid = %s AND project_file = %s AND status = 'returned'
            ORDER BY id DESC LIMIT 1
        """, (task_uid, project_file))
        returned = cur.fetchone()
        if returned:
            if not gip_note and returned['gip_note']:
                gip_note = returned['gip_note']
            if approved_percent is None and returned['approved_percent'] is not None:
                approved_percent = returned['approved_percent']
            if new_percent is None and returned['new_percent'] is not None:
                new_percent = returned['new_percent']
            cur.execute("""
                DELETE FROM sync_queue
                WHERE id = (SELECT id FROM sync_queue
                            WHERE task_uid = %s AND project_file = %s AND status = 'returned'
                            ORDER BY id DESC LIMIT 1)
            """, (task_uid, project_file))
            conn.commit()

        cur.execute("""
            INSERT INTO sync_queue
                (request_id, task_uid, project_file, text25, new_percent,
                 requested_percent, approved_percent, current_percent,
                 gip_note, status, created_by, created_at, task_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,NOW(),%s)
            RETURNING id, request_id, task_uid, project_file, text25, new_percent,
                      requested_percent, approved_percent, current_percent,
                      gip_note, status, created_at
        """, (request_id, task_uid, project_file, text25, new_percent,
              requested_percent, approved_percent, current_percent,
              gip_note or '', created_by, task_name))
        row = cur.fetchone()
        conn.commit()
        cur.close()
    finally:
        put_db(conn)
    sync_queue_to_json()
    return row['id']

def queue_update(entry_id, new_percent=None, gip_note=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        fields, vals = [], []
        if new_percent is not None:
            fields.append('new_percent=%s'); vals.append(new_percent)
        if gip_note is not None:
            fields.append('gip_note=%s'); vals.append(gip_note)
        if not fields:
            return False
        vals.append(entry_id)
        cur.execute(f"UPDATE sync_queue SET {', '.join(fields)} WHERE id=%s AND status='pending' RETURNING id", vals)
        row = cur.fetchone()
        conn.commit()
        cur.close()
        if not row:
            return False
    finally:
        put_db(conn)
    sync_queue_to_json()
    return True

def queue_delete(entry_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sync_queue WHERE id=%s AND status='pending' RETURNING id", (entry_id,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        if not row:
            return False
    finally:
        put_db(conn)
    sync_queue_to_json()
    return True

# ------------------------------------------------------------
# Middleware для метрик
# ------------------------------------------------------------
@app.before_request
def _before_req():
    request._start_time = time.time()

@app.after_request
def _after_req(resp):
    try:
        latency = time.time() - getattr(request, '_start_time', time.time())
        ep = request.endpoint or 'unknown'
        REQUEST_COUNT.labels(request.method, ep, resp.status_code).inc()
        REQUEST_LATENCY.labels(ep).observe(latency)
    except Exception:
        pass
    return resp

# ------------------------------------------------------------
# AUTH
# ------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            flash('Заполните все поля', 'danger')
            return redirect(url_for('login'))

        conn = get_db()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT id, username, password_hash, role, is_active FROM users WHERE username=%s", (username,))
            u = cur.fetchone()
            cur.close()
            if u and u['is_active'] and bcrypt.checkpw(password.encode(), u['password_hash'].encode()):
                user = User(u['id'], u['username'], u['role'], u['is_active'])
                login_user(user)
                session.permanent = True
                cur = conn.cursor()
                cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (u['id'],))
                conn.commit()
                cur.close()
                LOGIN_COUNT.labels('success').inc()
                audit('login_success')
                return redirect(url_for('index'))
            else:
                LOGIN_COUNT.labels('failed').inc()
                audit('login_failed', target=username, status='error')
                flash('Неверный логин или пароль', 'danger')
                return redirect(url_for('login'))
        finally:
            put_db(conn)
    return render_template('login.html', enable_registration=ENABLE_REGISTRATION)

@app.route('/register', methods=['POST'])
def register():
    if not ENABLE_REGISTRATION:
        flash('Регистрация отключена', 'warning')
        return redirect(url_for('login'))

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    role = request.form.get('role') or 'executor'
    if role not in ('executor', 'controller', 'gip'):
        role = 'executor'
    if len(username) < 3 or len(password) < 6:
        flash('Логин >= 3 символов, пароль >= 6 символов', 'danger')
        return redirect(url_for('login'))

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = get_db()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s,%s,%s,TRUE)",
                (username, pw_hash, role)
            )
            conn.commit()
            audit('user_registered', target=username, details={'role': role})
            flash('Регистрация выполнена. Войдите.', 'success')
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash('Пользователь уже существует', 'danger')
        cur.close()
    finally:
        put_db(conn)
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    audit('logout')
    logout_user()
    return redirect(url_for('login'))

# ------------------------------------------------------------
# HEALTH / METRICS
# ------------------------------------------------------------
@app.route('/health')
def health():
    status = {'status': 'ok', 'db': 'ok', 'queue_file': 'ok'}
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        put_db(conn)
    except Exception as e:
        status['status'] = 'error'
        status['db'] = f'error: {e}'
    if not os.path.exists(os.path.dirname(SYNC_QUEUE_FILE)):
        status['queue_file'] = 'missing'
    return jsonify(status), 200 if status['status'] == 'ok' else 500

@app.route('/metrics')
def metrics():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sync_queue WHERE status='pending'")
        QUEUE_SIZE_GAUGE.set(cur.fetchone()[0])
        cur.execute("SELECT COUNT(DISTINCT username) FROM audit_log WHERE timestamp > NOW() - INTERVAL '1 hour'")
        ACTIVE_USERS.set(cur.fetchone()[0])
        cur.close()
        put_db(conn)
    except Exception:
        pass
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

# ------------------------------------------------------------
# Главная
# ------------------------------------------------------------
@app.route('/')
@login_required
def index():
    xml_files = sorted([f for f in os.listdir(DATA_FOLDER) if f.lower().endswith('.xml')])
    return render_template(
        'dashboard.html',
        xml_files=xml_files,
        username=current_user.username,
        user_role=current_user.role,
    )

# ------------------------------------------------------------
# API: задачи
# ------------------------------------------------------------
@app.route('/api/tasks')
@login_required
def get_tasks():
    project_param = request.args.get('project', '')
    if project_param == 'all':
        all_tasks = []
        for xml_file in os.listdir(DATA_FOLDER):
            if xml_file.lower().endswith('.xml'):
                xml_path = os.path.join(DATA_FOLDER, xml_file)
                tasks = parse_project_xml(xml_path)
                for t in tasks:
                    t['project'] = xml_file
                all_tasks.extend(tasks)
        return jsonify(all_tasks)
    xml_path = os.path.join(DATA_FOLDER, project_param)
    if not os.path.exists(xml_path):
        return jsonify([])
    tasks = parse_project_xml(xml_path)
    for t in tasks:
        t['project'] = project_param
    return jsonify(tasks)

@app.route('/api/tasks_tree')
@login_required
def get_tasks_tree():
    project_param = request.args.get('project', '')
    if not project_param:
        return jsonify({'error': 'Project parameter required'}), 400

    if project_param == 'all':
        projects = []
        for xml_file in sorted(os.listdir(DATA_FOLDER)):
            if xml_file.lower().endswith('.xml'):
                xml_path = os.path.join(DATA_FOLDER, xml_file)
                tasks = parse_project_xml(xml_path)
                for t in tasks:
                    t['project'] = xml_file
                tree = build_task_tree(tasks)
                projects.append({
                    'name': xml_file,
                    'tree': tree,
                    'tasks_count': len(tasks)
                })
        return jsonify({'projects': projects})

    xml_path = os.path.join(DATA_FOLDER, project_param)
    if not os.path.exists(xml_path):
        return jsonify({'error': 'Project not found'}), 404

    tasks = parse_project_xml(xml_path)
    for t in tasks:
        t['project'] = project_param
    tree = build_task_tree(tasks)

    return jsonify({
        'projects': [{
            'name': project_param,
            'tree': tree,
            'tasks_count': len(tasks)
        }]
    })

@app.route('/api/task_by_uid')
@login_required
def task_by_uid():
    project_file = request.args.get('project_file')
    task_uid = request.args.get('task_uid')
    if not project_file or not task_uid:
        return jsonify({'error': 'Missing parameters'}), 400
    xml_path = os.path.join(DATA_FOLDER, project_file)
    if not os.path.exists(xml_path):
        return jsonify({'error': 'Project not found'}), 404
    tasks = parse_project_xml(xml_path)
    task = next((t for t in tasks if t['uid'] == task_uid), None)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    task['project'] = project_file
    return jsonify(task)

# ------------------------------------------------------------
# API: комментарии и история
# ------------------------------------------------------------
@app.route('/api/comment', methods=['POST'])
@login_required
def add_comment():
    data = request.json
    role = current_user.role
    task_name = data.get('task_name', '')
    if not task_name and data.get('task_uid') and data.get('project_file'):
        try:
            xml_path = os.path.join(DATA_FOLDER, data['project_file'])
            if os.path.exists(xml_path):
                tasks = parse_project_xml(xml_path)
                task = next((t for t in tasks if t['uid'] == data['task_uid']), None)
                if task:
                    task_name = task.get('name', '')
        except Exception:
            pass
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO comments (task_uid, project_file, user_id, user_role, username, comment_text, task_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (data['task_uid'], data['project_file'], current_user.id, role,
              current_user.username, data['comment'], task_name))
        conn.commit()
        cur.close()
        put_db(conn)
        log_action(role, 'add_comment',
                   project_file=data['project_file'], task_uid=data['task_uid'],
                   task_name=task_name,
                   details={'comment': data['comment'], 'user': current_user.username})
        audit('add_comment',
              target=data['task_uid'],
              details={'comment': data['comment'], 'project': data['project_file'], 'task_name': task_name},
              status='success')
        return jsonify({'status': 'ok', 'task_name': task_name})
    except Exception as e:
        slog.error("add_comment_error", error=str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/comments/<task_uid>/<path:project_file>')
@login_required
def get_comments(task_uid, project_file):
    conn = get_db()
    try:
        cur = conn.cursor()
        from urllib.parse import unquote
        project_file_decoded = unquote(project_file)
        cur.execute("""
            SELECT user_role, username, comment_text, timestamp, task_name
            FROM comments
            WHERE task_uid=%s AND project_file=%s
            ORDER BY timestamp DESC
        """, (task_uid, project_file_decoded))
        rows = cur.fetchall()
        cur.close()
        return jsonify([
            {'role': r[0], 'username': r[1], 'text': r[2],
             'timestamp': r[3].isoformat() if r[3] else None,
             'task_name': r[4] or ''}
            for r in rows
        ])
    finally:
        put_db(conn)

@app.route('/api/task_history/<task_uid>/<path:project_file>')
@login_required
def get_task_history(task_uid, project_file):
    conn = get_db()
    try:
        cur = conn.cursor()
        from urllib.parse import unquote
        project_file_decoded = unquote(project_file)
        cur.execute("""
            SELECT requested_percent, approved_percent, status, created_by, timestamp, reviewed_at
            FROM percent_requests
            WHERE task_uid=%s AND project_file=%s
            ORDER BY timestamp DESC LIMIT 20
        """, (task_uid, project_file_decoded))
        rows = cur.fetchall()
        cur.close()
        return jsonify([{
            'requested_percent': r[0], 'approved_percent': r[1], 'status': r[2],
            'created_by': r[3],
            'timestamp': r[4].isoformat() if r[4] else None,
            'reviewed_at': r[5].isoformat() if r[5] else None,
        } for r in rows])
    finally:
        put_db(conn)

@app.route('/api/percent_history/<task_uid>/<path:project_file>')
@login_required
def get_percent_history(task_uid, project_file):
    conn = get_db()
    try:
        cur = conn.cursor()
        from urllib.parse import unquote
        project_file_decoded = unquote(project_file)
        cur.execute("""
            SELECT old_percent, new_percent, changed_by, changed_at, source
            FROM task_percent_history
            WHERE task_uid=%s AND project_file=%s
            ORDER BY changed_at DESC
        """, (task_uid, project_file_decoded))
        rows = cur.fetchall()
        cur.close()
        return jsonify([{
            'old_percent': r[0], 'new_percent': r[1],
            'changed_by': r[2],
            'changed_at': r[3].isoformat() if r[3] else None,
            'source': r[4],
        } for r in rows])
    finally:
        put_db(conn)

# ------------------------------------------------------------
# API: заявки на процент
# ------------------------------------------------------------
@app.route('/api/request_percent', methods=['POST'])
@login_required
def request_percent():
    data = request.json
    role = current_user.role
    project_file = data['project_file']
    task_uid = data['task_uid']
    percent = int(data['percent'])
    text25 = data.get('text25', '')
    task_name = data.get('task_name', '')

    if role == 'executor':
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO percent_requests
                    (task_uid, project_file, requested_percent, status, created_by)
                VALUES (%s,%s,%s,'pending',%s)
            """, (task_uid, project_file, percent, current_user.username))
            conn.commit()
            cur.close()
            log_action(role, 'request_percent_change',
                       project_file=project_file, task_uid=task_uid,
                       task_name=task_name,
                       details={'requested_percent': percent, 'text25': text25,
                                'user': current_user.username})
            audit('request_percent', target=task_uid,
                  details={'percent': percent, 'project': project_file})
            return jsonify({'status': 'pending'})
        finally:
            put_db(conn)

    current_pct = get_task_current_percent(project_file, task_uid)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO percent_requests
                (task_uid, project_file, requested_percent, approved_percent,
                 status, created_by, reviewed_by, reviewed_at)
            VALUES (%s,%s,%s,%s,'approved',%s,%s,NOW())
            RETURNING id
        """, (task_uid, project_file, percent, percent,
              current_user.username, current_user.username))
        req_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        put_db(conn)
    queue_insert(req_id, task_uid, project_file, text25, percent,
                 requested_percent=percent, approved_percent=percent,
                 current_percent=current_pct, created_by=current_user.username,
                 task_name=task_name)
    log_action(role, 'apply_percent_direct',
               project_file=project_file, task_uid=task_uid,
               task_name=task_name,
               details={'new_percent': percent, 'text25': text25,
                        'user': current_user.username})
    audit('apply_percent_direct', target=task_uid,
          details={'percent': percent, 'project': project_file})
    return jsonify({'status': 'queued'})

@app.route('/api/approve_percent', methods=['POST'])
@role_required('controller', 'gip')
def approve_percent():
    data = request.json
    request_id = data['request_id']
    task_uid = data['task_uid']
    project_file = data['project_file']
    text25 = data['text25']
    approved_percent = int(data['approved_percent'])
    task_name = data.get('task_name', '')

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT requested_percent FROM percent_requests WHERE id=%s", (request_id,))
        row = cur.fetchone()
        requested_percent = row[0] if row else None
        current_pct = get_task_current_percent(project_file, task_uid)
        cur.execute("""
            UPDATE percent_requests
               SET status='approved', approved_percent=%s,
                   reviewed_by=%s, reviewed_at=NOW()
             WHERE id=%s
        """, (approved_percent, current_user.username, request_id))
        conn.commit()
        cur.close()
    finally:
        put_db(conn)

    entry_id = queue_insert(
        request_id, task_uid, project_file, text25, approved_percent,
        requested_percent=requested_percent, approved_percent=approved_percent,
        current_percent=current_pct, created_by=current_user.username,
        task_name=task_name
    )
    log_action(current_user.role, 'approve_percent', project_file, task_uid, task_name,
               {'approved_percent': approved_percent, 'text25': text25,
                'queue_id': entry_id, 'user': current_user.username})
    audit('approve_percent', target=task_uid,
          details={'approved': approved_percent, 'queue_id': entry_id})
    return jsonify({'status': 'approved', 'message': 'Добавлено в очередь'})

@app.route('/api/reject_request', methods=['POST'])
@role_required('controller', 'gip')
def reject_request():
    data = request.json
    request_id = data['request_id']
    reason = data.get('reason', '')
    task_name = data.get('task_name', '')
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE percent_requests
               SET status='rejected', reviewed_by=%s, reviewed_at=NOW()
             WHERE id=%s
        """, (current_user.username, request_id))
        conn.commit()
        cur.close()
        put_db(conn)
        log_action(current_user.role, 'reject_request', task_name=task_name,
                   details={'reason': reason, 'request_id': request_id,
                            'user': current_user.username})
        audit('reject_request', target=str(request_id), details={'reason': reason})
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ------------------------------------------------------------
# API: статистика / pending / logs
# ------------------------------------------------------------
@app.route('/api/stats')
@login_required
def get_stats():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM percent_requests WHERE status='pending'")
        pending_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM percent_requests WHERE status='approved'")
        approved_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM percent_requests WHERE status='rejected'")
        rejected_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM comments")
        comments_count = cur.fetchone()[0]
        cur.close()
        return jsonify({'pending': pending_count, 'approved': approved_count,
                        'rejected': rejected_count, 'comments': comments_count})
    finally:
        put_db(conn)

@app.route('/api/logs')
@role_required('controller', 'gip')
def get_logs():
    limit = request.args.get('limit', 200, type=int)
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        result = []
        for r in rows:
            e = dict(r)
            if e.get('timestamp'):
                e['timestamp'] = e['timestamp'].isoformat()
            result.append(e)
        return jsonify(result)
    finally:
        put_db(conn)

@app.route('/api/pending_requests')
@role_required('controller', 'gip')
def get_pending_requests():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, task_uid, project_file, requested_percent, timestamp
            FROM percent_requests WHERE status='pending'
            ORDER BY timestamp DESC
        """)
        rows = cur.fetchall()
        cur.close()
    finally:
        put_db(conn)
    out = []
    for req_id, task_uid, project_file, req_percent, ts in rows:
        xml_path = os.path.join(DATA_FOLDER, project_file)
        if not os.path.exists(xml_path):
            continue
        tasks = parse_project_xml(xml_path)
        task = next((t for t in tasks if t['uid'] == task_uid), None)
        if task:
            task['project'] = project_file
            task['request_id'] = req_id
            task['requested_percent'] = req_percent
            task['request_timestamp'] = ts.isoformat() if ts else None
            out.append(task)
    return jsonify(out)

# ------------------------------------------------------------
# API: очередь синхронизации
# ------------------------------------------------------------
@app.route('/api/pending_sync_json')
@role_required('controller', 'gip')
def pending_sync_json():
    project_filter = request.args.get('project')
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if project_filter:
            cur.execute("""SELECT * FROM sync_queue WHERE status='pending' AND project_file=%s
                           ORDER BY id""", (project_filter,))
        else:
            cur.execute("SELECT * FROM sync_queue WHERE status='pending' ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        return jsonify([_row_to_entry(r) for r in rows])
    finally:
        put_db(conn)

@app.route('/api/pending_sync_json/<int:entry_id>', methods=['PUT'])
@role_required('controller', 'gip')
def update_pending_sync_json(entry_id):
    data = request.json
    new_percent = data.get('new_percent')
    gip_note = data.get('gip_note')
    comment = data.get('comment')
    task_uid = data.get('task_uid')
    project_file = data.get('project_file')
    task_name = data.get('task_name', '')

    if comment and task_uid and project_file:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO comments (task_uid, project_file, user_id, user_role, username, comment_text, task_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (task_uid, project_file, current_user.id, current_user.role,
                  current_user.username, comment, task_name))
            conn.commit()
            cur.close()
            put_db(conn)
            log_action(current_user.role, 'add_comment_sync',
                       project_file=project_file, task_uid=task_uid,
                       task_name=task_name,
                       details={'comment': comment, 'source': 'sync_modal', 'user': current_user.username})
        except Exception as e:
            slog.error("add_comment_sync_error", error=str(e))

    if new_percent is not None:
        try:
            new_percent = int(new_percent)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid percent'}), 400
        if new_percent < 0 or new_percent > 100:
            return jsonify({'error': 'Invalid percent value'}), 400

    if queue_update(entry_id, new_percent, gip_note):
        log_action(current_user.role, 'update_sync_queue',
                   details={'entry_id': entry_id, 'new_percent': new_percent,
                            'gip_note': gip_note, 'user': current_user.username})
        audit('update_sync_queue', target=str(entry_id),
              details={'new_percent': new_percent, 'gip_note': gip_note})
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Entry not found'}), 404

@app.route('/api/pending_sync_json/<int:entry_id>', methods=['DELETE'])
@role_required('controller', 'gip')
def delete_pending_sync_json(entry_id):
    if queue_delete(entry_id):
        log_action(current_user.role, 'delete_pending_sync',
                   details={'entry_id': entry_id, 'user': current_user.username})
        audit('delete_pending_sync', target=str(entry_id))
        return jsonify({'status': 'ok', 'message': 'Запись удалена'})
    return jsonify({'error': 'Entry not found'}), 404

@app.route('/api/pending_sync_json/<int:entry_id>/return', methods=['POST'])
@role_required('controller', 'gip')
def return_pending_sync_json(entry_id):
    data = request.json or {}
    comment = data.get('comment')
    task_uid = data.get('task_uid')
    project_file = data.get('project_file')
    task_name = data.get('task_name', '')
    new_percent = data.get('new_percent')
    gip_note = data.get('gip_note')

    if new_percent is not None or gip_note is not None:
        queue_update(entry_id, new_percent, gip_note)

    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM sync_queue WHERE id = %s AND status = 'pending'", (entry_id,))
        entry = cur.fetchone()
        if not entry:
            return jsonify({'error': 'Запись не найдена или уже не в ожидании'}), 404

        requested_percent = entry.get('requested_percent') or entry.get('approved_percent') or entry.get('new_percent')
        cur.execute("""
            INSERT INTO percent_requests
                (task_uid, project_file, requested_percent, status, created_by, reviewed_by, reviewed_at)
            VALUES (%s, %s, %s, 'pending', %s, NULL, NULL)
            RETURNING id
        """, (task_uid, project_file, requested_percent, current_user.username))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Не удалось создать заявку'}), 500
        new_request_id = row['id']

        cur.execute("""
            UPDATE sync_queue
            SET status = 'returned', processed_at = NOW()
            WHERE id = %s
        """, (entry_id,))
        conn.commit()
        cur.close()
    finally:
        put_db(conn)

    try:
        conn = get_db()
        cur = conn.cursor()
        system_comment = f"[ВОЗВРАТ] Возвращено из очереди синхронизации (создана новая заявка №{new_request_id})"
        cur.execute("""
            INSERT INTO comments (task_uid, project_file, user_id, user_role, username, comment_text, task_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (task_uid, project_file, current_user.id, current_user.role,
              current_user.username, system_comment, task_name))

        if comment and comment.strip():
            cur.execute("""
                INSERT INTO comments (task_uid, project_file, user_id, user_role, username, comment_text, task_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (task_uid, project_file, current_user.id, current_user.role,
                  current_user.username, comment, task_name))
            slog.info("return_user_comment_added", comment=comment, task_uid=task_uid)
        else:
            slog.info("return_no_user_comment")

        conn.commit()
        cur.close()
        log_action(current_user.role, 'return_from_sync_with_new_request',
                   project_file=project_file, task_uid=task_uid,
                   task_name=task_name,
                   details={'entry_id': entry_id, 'new_request_id': new_request_id, 'comment': comment})
    except Exception as e:
        slog.error("return_comment_error", error=str(e))
    finally:
        put_db(conn)

    sync_queue_to_json()
    return jsonify({'status': 'returned', 'message': f'Создана новая заявка #{new_request_id} для контроллёра'})

@app.route('/api/processed_sync')
@role_required('controller', 'gip')
def processed_sync():
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM processed_sync ORDER BY processed_at DESC LIMIT 200")
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            e = dict(r)
            if e.get('processed_at'):
                e['processed_at'] = e['processed_at'].isoformat()
            out.append(e)
        return jsonify(out)
    finally:
        put_db(conn)

# ------------------------------------------------------------
# Старт
# ------------------------------------------------------------
def startup():
    slog.info("startup", data_folder=DATA_FOLDER, queue_file=SYNC_QUEUE_FILE)
    os.makedirs(LOG_FOLDER, exist_ok=True)
    os.makedirs(os.path.dirname(SYNC_QUEUE_FILE), exist_ok=True)
    sync_queue_to_json()

if __name__ == '__main__':
    startup()
    slog.info("server_starting", port=PORT)
    serve(app, host='0.0.0.0', port=PORT, threads=25, connection_limit=100)