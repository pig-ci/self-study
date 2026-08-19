"""
自主學習時間規劃平台 - 後端伺服器
用法: python3 app.py
"""
import os
import sqlite3
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

DB_PATH = os.environ.get('DB_PATH', 'school.db')
JWT_SECRET = os.environ.get('JWT_SECRET', 'school-self-study-secret-2024')
PORT = int(os.environ.get('PORT', 3000))


# ── 資料庫 ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            display_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student'
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            content TEXT NOT NULL,
            goal TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)

    # 預設老師帳號
    existing = conn.execute("SELECT id FROM users WHERE username = 'teacher'").fetchone()
    if not existing:
        hashed = bcrypt.hashpw(b'teacher123', bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password, display_name, class_name, role) VALUES (?, ?, ?, ?, ?)",
            ('teacher', hashed, '老師', '教師', 'teacher')
        )
        print("✅ 預設老師帳號建立：teacher / teacher123")
    conn.commit()
    conn.close()


# ── JWT 驗證 ──────────────────────────────────────────────
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': '請先登入'}), 401
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            request.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token 已過期，請重新登入'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token 無效'}), 401
        return f(*args, **kwargs)
    return decorated



# ── 靜態頁面 ──────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


# ── 帳號 API ──────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    display_name = (data.get('display_name') or '').strip()
    class_name = (data.get('class_name') or '').strip()

    if not all([username, password, display_name, class_name]):
        return jsonify({'error': '所有欄位皆必填'}), 400

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = get_db()
        result = conn.execute(
            "INSERT INTO users (username, password, display_name, class_name, role) VALUES (?, ?, ?, ?, 'student')",
            (username, hashed, display_name, class_name)
        )
        conn.commit()
        uid = result.lastrowid
        conn.close()
        return jsonify({'id': uid, 'display_name': display_name, 'class_name': class_name})
    except sqlite3.IntegrityError:
        return jsonify({'error': '帳號已存在'}), 400


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '')
    password = data.get('password', '')

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not user or not bcrypt.checkpw(password.encode(), user['password'].encode()):
        return jsonify({'error': '帳號或密碼錯誤'}), 401

    payload = {
        'id': user['id'],
        'username': user['username'],
        'display_name': user['display_name'],
        'class_name': user['class_name'],
        'role': user['role'],
        'exp': datetime.utcnow() + timedelta(hours=8)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
    user_info = {k: user[k] for k in ['id', 'username', 'display_name', 'class_name', 'role']}
    return jsonify({'token': token, 'user': user_info})


@app.route('/api/me')
@token_required
def me():
    return jsonify(request.user)


@app.route('/api/users')
@token_required
def get_users():
    conn = get_db()
    users = conn.execute(
        "SELECT id, username, display_name, class_name, role FROM users ORDER BY class_name, display_name"
    ).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])


# ── 計劃 API ──────────────────────────────────────────────
@app.route('/api/plans', methods=['POST'])
@token_required
def submit_plan():
    data = request.json or {}
    date = data.get('date', '').strip()
    slots = data.get('slots', [])

    if not date or not slots:
        return jsonify({'error': '請填寫日期與至少一個時段'}), 400

    conn = get_db()
    conn.execute("DELETE FROM plans WHERE user_id = ? AND date = ?", (request.user['id'], date))

    for s in slots:
        if not s.get('time_start') or not s.get('time_end') or not s.get('content', '').strip():
            conn.close()
            return jsonify({'error': '每個時段都需填寫開始、結束時間與內容'}), 400
        try:
            sh, sm = map(int, s['time_start'].split(':'))
            eh, em = map(int, s['time_end'].split(':'))
            if sh * 60 + sm >= eh * 60 + em:
                conn.close()
                return jsonify({'error': f"時段 {s['time_start']} 的結束時間必須晚於開始時間"}), 400
        except (ValueError, AttributeError):
            conn.close()
            return jsonify({'error': '時間格式錯誤，請使用 HH:MM 格式'}), 400
        conn.execute(
            "INSERT INTO plans (user_id, date, time_start, time_end, content, goal) VALUES (?, ?, ?, ?, ?, ?)",
            (request.user['id'], date, s['time_start'], s['time_end'], s['content'].strip(), s.get('goal', '').strip())
        )

    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/plans/me')
@token_required
def my_plans():
    date = request.args.get('date', '')
    conn = get_db()
    if date:
        rows = conn.execute(
            "SELECT * FROM plans WHERE user_id = ? AND date = ? ORDER BY time_start",
            (request.user['id'], date)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM plans WHERE user_id = ? ORDER BY date DESC, time_start",
            (request.user['id'],)
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/plans/all')
@token_required
def all_plans():
    date = request.args.get('date', '')
    conn = get_db()
    if date:
        rows = conn.execute(
            """SELECT p.*, u.display_name, u.class_name FROM plans p
               JOIN users u ON p.user_id = u.id
               WHERE p.date = ? ORDER BY u.class_name, u.display_name, p.time_start""",
            (date,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT p.*, u.display_name, u.class_name FROM plans p
               JOIN users u ON p.user_id = u.id
               ORDER BY p.date DESC, u.class_name, u.display_name, p.time_start"""
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/plans', methods=['DELETE'])
@token_required
def delete_plan():
    data = request.json or {}
    date = data.get('date', '')
    conn = get_db()
    conn.execute("DELETE FROM plans WHERE user_id = ? AND date = ?", (request.user['id'], date))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── 啟動 ──────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    print(f"🚀 伺服器啟動於 http://localhost:{PORT}")
    print(f"📖 預設老師帳號：teacher / teacher123")
    app.run(host='0.0.0.0', port=PORT, debug=False)
