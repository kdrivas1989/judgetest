#!/usr/bin/env python3
"""USPA Judge Test - Web-based testing application for USPA judges."""

import os
import uuid
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g

# Load .env file if it exists
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
from functools import wraps
from questions import TESTS as DEFAULT_TESTS  # Fallback for initial seeding


def normalize_section_ref(section):
    """Normalize section reference to allow formatting flexibility.

    Handles variations like:
    - "8-1.3.1" vs "8.1.3.1" vs "8 1 3 1"
    - "Section 8-1.3.1" vs "8-1.3.1"
    - "Sec. 8-1.3.1" vs "8-1.3.1"
    - Trailing punctuation
    - Various dash types (em dash, en dash, hyphen)
    """
    if not section:
        return ''

    s = section.strip().lower()

    # Remove common prefixes
    prefixes = ['section', 'sec.', 'sec', 'ch.', 'ch', 'chapter']
    for prefix in prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()

    # Normalize various dash types to hyphen
    s = re.sub(r'[–—−]', '-', s)  # en dash, em dash, minus sign -> hyphen

    # Remove all separators and spaces, keep only alphanumeric
    s = re.sub(r'[\s.\-_]+', '', s)

    # Remove trailing punctuation
    s = s.rstrip('.,;:')

    return s

import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'uspa-judge-test-secret-key-change-in-production')
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'judgetest.db')
os.makedirs(os.path.dirname(DATABASE_PATH) or '.', exist_ok=True)

# Categories based on chapters
CATEGORIES = {
    'al': {'name': 'AL', 'tests': ['ch8_regional', 'ch8_national']},
    'fs': {'name': 'FS', 'tests': ['ch9_regional', 'ch9_national']},
    'cf': {'name': 'CF', 'tests': ['ch10_regional', 'ch10_national']},
    'ae': {'name': 'AE', 'tests': ['ch11_regional', 'ch11_national']},
    'cp': {'name': 'CP', 'tests': ['ch12_13_regional', 'ch12_13_national']},
    'ws': {'name': 'WS', 'tests': ['ch14_regional', 'ch14_national']},
    'sp': {'name': 'SP', 'tests': ['ch15_regional', 'ch15_national']},
}

# General test available to all proctors
GENERAL_TEST_ID = 'general'

# Proctor levels (Regional can only administer Regional tests, National can administer both, Examiner can administer both + examine judges)
PROCTOR_LEVELS = ['regional', 'national', 'examiner']

# User roles
USER_ROLES = ['student', 'proctor', 'jwg', 'admin']

# Email configuration
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME', 'USPA Judge Test')
EMAIL_FROM_ADDR = os.environ.get('EMAIL_FROM_ADDR', 'kevin@kd-evolution.com')
# Legacy SMTP config (fallback for local dev)
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', SMTP_USERNAME)

import urllib.request


def send_login_email(to_email, name, username, password, role='member'):
    """Send login credentials via email."""
    site_url = os.environ.get('SITE_URL', 'http://localhost:5000')
    body_text = f"""Hello {name},

You have been added to the USPA Judge Test system.

Your login credentials:
  Email: {username}
  Password: {password}

Please log in at: {site_url}

We recommend changing your password after your first login.

- USPA Judge Test Admin
"""

    # Try Brevo HTTP API first (works on Railway, 300 emails/day free)
    if BREVO_API_KEY:
        try:
            payload = json.dumps({
                'sender': {'name': EMAIL_FROM_NAME, 'email': EMAIL_FROM_ADDR},
                'to': [{'email': to_email, 'name': name}],
                'subject': 'USPA Judge Test - Your Login Information',
                'textContent': body_text
            }).encode('utf-8')
            req = urllib.request.Request(
                'https://api.brevo.com/v3/smtp/email',
                data=payload,
                headers={
                    'api-key': BREVO_API_KEY,
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
            )
            resp = urllib.request.urlopen(req, timeout=10)
            return True, 'Email sent successfully (Brevo)'
        except Exception as e:
            return False, f'Brevo failed: {e}'

    # Fallback to SMTP for local development
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return False, 'Email not configured. Set BREVO_API_KEY or SMTP credentials.'

    msg = MIMEMultipart()
    msg['From'] = SMTP_FROM_EMAIL
    msg['To'] = to_email
    msg['Subject'] = 'USPA Judge Test - Your Login Information'
    msg.attach(MIMEText(body_text, 'plain'))

    for method in ['ssl', 'starttls']:
        try:
            if method == 'ssl':
                server = smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=10)
            else:
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
            server.quit()
            return True, f'Email sent successfully ({method})'
        except Exception as e:
            last_error = e
            continue
    return False, str(last_error)


def get_sqlite_db():
    """Get SQLite database connection for local development."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Close database connection at end of request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database with tables and default users."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            name TEXT NOT NULL,
            categories TEXT DEFAULT '[]',
            assigned_tests TEXT DEFAULT '[]',
            proctor_level TEXT DEFAULT 'regional',
            expiration_date TEXT DEFAULT ''
        )
    ''')

    # Add last_login column if not exists
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'last_login' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_login TEXT DEFAULT ''")

    # Create test_results table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            result_id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    ''')

    # Create tests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            chapter TEXT NOT NULL,
            passing_score INTEGER NOT NULL,
            questions TEXT NOT NULL
        )
    ''')

    # Create custom_questions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS custom_questions (
            test_id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    ''')

    # Create question_verifications table for JWG
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS question_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            verified_by TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            verifier_name TEXT NOT NULL,
            UNIQUE(test_id, question_id)
        )
    ''')

    # Create question_changes table for JWG edit audit trail
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS question_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            changed_by TEXT NOT NULL,
            changer_name TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            changes TEXT NOT NULL
        )
    ''')

    # Create question_flags table for JWG flagged questions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS question_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            flagged_by TEXT NOT NULL,
            flagged_at TEXT NOT NULL,
            flagger_name TEXT NOT NULL,
            UNIQUE(test_id, question_id)
        )
    ''')

    # Add default admin if not exists
    cursor.execute('SELECT username FROM users WHERE username = ?', ('admin',))
    if not cursor.fetchone():
        cursor.execute(
            'INSERT INTO users (username, password, role, name, categories, assigned_tests) VALUES (?, ?, ?, ?, ?, ?)',
            ('admin', 'admin123', 'admin', 'Administrator', '[]', '[]')
        )

    # Seed default JWG members if not exists
    jwg_members = [
        ('crishoward4@gmail.com', 'Cris Howard'),
        ('near.h.nee@gmail.com', 'Hao Ni'),
        ('jrees@uspa.org', 'Jim Rees'),
        ('ironeddie42@gmail.com', 'Steve Hubbard'),
        ('sudeepkodavati@gmail.com', 'Sudeep Kodavati'),
        ('bryce@robotlords.com', 'Bryce Witcher'),
        ('aowens@uspa.org', 'Amanda Owens'),
        ('kdrivas1989@gmail.com', 'Kevin Drivas'),
    ]
    for email, name in jwg_members:
        cursor.execute('SELECT username FROM users WHERE username = ?', (email,))
        if not cursor.fetchone():
            cursor.execute(
                'INSERT INTO users (username, password, role, name, categories, assigned_tests) VALUES (?, ?, ?, ?, ?, ?)',
                (email, 'password', 'jwg,admin' if email == 'kdrivas1989@gmail.com' else 'jwg', name, '[]', '[]')
            )

    # Ensure kdrivas1989@gmail.com has admin and jwg roles
    cursor.execute('SELECT role FROM users WHERE username = ?', ('kdrivas1989@gmail.com',))
    row = cursor.fetchone()
    if row:
        role_str = row[0]
        if 'admin' not in role_str:
            role_str = role_str + ',admin'
        if 'jwg' not in role_str:
            role_str = role_str + ',jwg'
        if role_str != row[0]:
            cursor.execute('UPDATE users SET role = ? WHERE username = ?',
                           (role_str, 'kdrivas1989@gmail.com'))

    # Auto-seed any missing tests from DEFAULT_TESTS
    cursor.execute('SELECT test_id FROM tests')
    existing_tests = {row[0] for row in cursor.fetchall()}
    missing = [tid for tid in DEFAULT_TESTS if tid not in existing_tests]
    if missing:
        for test_id in missing:
            test_data = DEFAULT_TESTS[test_id]
            cursor.execute('''
                INSERT OR REPLACE INTO tests (test_id, name, chapter, passing_score, questions)
                VALUES (?, ?, ?, ?, ?)
            ''', (test_id, test_data['name'], test_data['chapter'],
                  test_data['passing_score'], json.dumps(test_data['questions'])))
        print(f"Auto-seeded {len(missing)} missing tests to database: {missing}")

    conn.commit()
    conn.close()


def get_user(username):
    """Get user from database."""
    db = get_sqlite_db()
    cursor = db.execute('SELECT * FROM users WHERE username = ?', (username,))
    row = cursor.fetchone()
    if row:
        return {
            'password': row['password'],
            'role': row['role'],
            'name': row['name'],
            'categories': json.loads(row['categories']),
            'assigned_tests': json.loads(row['assigned_tests']) if row['assigned_tests'] else [],
            'proctor_level': row['proctor_level'] or 'regional',
            'expiration_date': row['expiration_date'] or '',
            'last_login': row['last_login'] or ''
        }
    return None


def get_all_users():
    """Get all users from database."""
    db = get_sqlite_db()
    cursor = db.execute('SELECT * FROM users')
    users = {}
    for row in cursor.fetchall():
        users[row['username']] = {
            'password': row['password'],
            'role': row['role'],
            'name': row['name'],
            'categories': json.loads(row['categories']),
            'assigned_tests': json.loads(row['assigned_tests']) if row['assigned_tests'] else [],
            'proctor_level': row['proctor_level'] or 'regional',
            'expiration_date': row['expiration_date'] or '',
            'last_login': row['last_login'] or ''
        }
    return users


def save_user(username, user_data):
    """Save user to database."""
    categories = json.dumps(user_data.get('categories', []))
    assigned_tests = json.dumps(user_data.get('assigned_tests', []))
    proctor_level = user_data.get('proctor_level', 'regional')
    expiration_date = user_data.get('expiration_date', '') or ''
    last_login = user_data.get('last_login', '') or ''
    db = get_sqlite_db()
    db.execute('''
        INSERT OR REPLACE INTO users (username, password, role, name, categories, assigned_tests, proctor_level, expiration_date, last_login)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (username, user_data['password'], user_data['role'], user_data['name'], categories, assigned_tests, proctor_level, expiration_date, last_login))
    db.commit()


def has_role(user_or_role_str, role):
    """Check if a user (or role string) has a given role."""
    if isinstance(user_or_role_str, dict):
        role_str = user_or_role_str.get('role', '')
    else:
        role_str = user_or_role_str or ''
    return role in [r.strip() for r in role_str.split(',')]


def add_role(existing_role_str, new_role):
    """Add a role to a comma-separated role string if not already present."""
    roles = [r.strip() for r in (existing_role_str or '').split(',') if r.strip()]
    if new_role not in roles:
        roles.append(new_role)
    return ','.join(roles)


def remove_role(existing_role_str, role_to_remove):
    """Remove a role from a comma-separated role string."""
    roles = [r.strip() for r in (existing_role_str or '').split(',') if r.strip()]
    roles = [r for r in roles if r != role_to_remove]
    return ','.join(roles)


def delete_user(username):
    """Delete user from database."""
    db = get_sqlite_db()
    db.execute('DELETE FROM users WHERE username = ?', (username,))
    db.commit()


def get_test_result(result_id):
    """Get test result from database."""
    db = get_sqlite_db()
    cursor = db.execute('SELECT data FROM test_results WHERE result_id = ?', (result_id,))
    row = cursor.fetchone()
    if row:
        return json.loads(row['data'])
    return None


def get_all_test_results():
    """Get all test results from database."""
    db = get_sqlite_db()
    cursor = db.execute('SELECT result_id, data FROM test_results')
    results = {}
    for row in cursor.fetchall():
        results[row['result_id']] = json.loads(row['data'])
    return results


def save_test_result(result_id, result_data):
    """Save test result to database."""
    db = get_sqlite_db()
    db.execute(
        'INSERT OR REPLACE INTO test_results (result_id, data) VALUES (?, ?)',
        (result_id, json.dumps(result_data))
    )
    db.commit()


def get_custom_questions(test_id):
    """Get custom questions for a test from database."""
    db = get_sqlite_db()
    cursor = db.execute('SELECT data FROM custom_questions WHERE test_id = ?', (test_id,))
    row = cursor.fetchone()
    if row:
        return json.loads(row['data'])
    return None


def save_custom_questions(test_id, questions_data):
    """Save custom questions for a test to database."""
    db = get_sqlite_db()
    db.execute(
        'INSERT OR REPLACE INTO custom_questions (test_id, data) VALUES (?, ?)',
        (test_id, json.dumps(questions_data))
    )
    db.commit()


def get_question_verifications(test_id=None):
    """Get question verifications, optionally filtered by test_id."""
    db = get_sqlite_db()
    if test_id:
        cursor = db.execute('SELECT * FROM question_verifications WHERE test_id = ?', (test_id,))
    else:
        cursor = db.execute('SELECT * FROM question_verifications')
    verifications = {}
    for row in cursor.fetchall():
        key = f"{row['test_id']}_{row['question_id']}"
        verifications[key] = {
            'verified_by': row['verified_by'],
            'verified_at': row['verified_at'],
            'verifier_name': row['verifier_name']
        }
    return verifications


def save_question_verification(test_id, question_id, username, name):
    """Save a question verification."""
    verified_at = datetime.now().isoformat()
    db = get_sqlite_db()
    db.execute('''
        INSERT OR REPLACE INTO question_verifications
        (test_id, question_id, verified_by, verified_at, verifier_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (test_id, question_id, username, verified_at, name))
    db.commit()
    return True


def remove_question_verification(test_id, question_id):
    """Remove a question verification."""
    db = get_sqlite_db()
    db.execute('DELETE FROM question_verifications WHERE test_id = ? AND question_id = ?', (test_id, question_id))
    db.commit()
    return True


def save_question_change(test_id, question_id, username, name, changes):
    """Save a question change record for audit trail."""
    changed_at = datetime.now().isoformat()
    db = get_sqlite_db()
    db.execute('''
        INSERT INTO question_changes
        (test_id, question_id, changed_by, changer_name, changed_at, changes)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (test_id, question_id, username, name, changed_at, json.dumps(changes)))
    db.commit()


def get_question_changes(test_id, question_id):
    """Get change history for a question, newest first."""
    db = get_sqlite_db()
    cursor = db.execute(
        'SELECT * FROM question_changes WHERE test_id = ? AND question_id = ? ORDER BY changed_at DESC',
        (test_id, question_id)
    )
    changes = []
    for row in cursor.fetchall():
        changes.append({
            'id': row['id'],
            'changed_by': row['changed_by'],
            'changer_name': row['changer_name'],
            'changed_at': row['changed_at'],
            'changes': json.loads(row['changes'])
        })
    return changes


def get_question_flags(test_id=None):
    """Get question flags, optionally filtered by test_id."""
    db = get_sqlite_db()
    if test_id:
        cursor = db.execute('SELECT * FROM question_flags WHERE test_id = ?', (test_id,))
    else:
        cursor = db.execute('SELECT * FROM question_flags')
    flags = {}
    for row in cursor.fetchall():
        key = f"{row['test_id']}_{row['question_id']}"
        flags[key] = {
            'flagged_by': row['flagged_by'],
            'flagged_at': row['flagged_at'],
            'flagger_name': row['flagger_name']
        }
    return flags


def save_question_flag(test_id, question_id, username, name):
    """Save a question flag."""
    flagged_at = datetime.now().isoformat()
    db = get_sqlite_db()
    db.execute('''
        INSERT OR REPLACE INTO question_flags
        (test_id, question_id, flagged_by, flagged_at, flagger_name)
        VALUES (?, ?, ?, ?, ?)
    ''', (test_id, question_id, username, flagged_at, name))
    db.commit()
    return True


def remove_question_flag(test_id, question_id):
    """Remove a question flag."""
    db = get_sqlite_db()
    db.execute('DELETE FROM question_flags WHERE test_id = ? AND question_id = ?', (test_id, question_id))
    db.commit()
    return True


def get_test_questions(test_id):
    """Get questions for a test from database."""
    # First try to get from tests table in database
    test = get_test(test_id)
    if test and test.get('questions'):
        return test['questions']
    # Fallback to default if not in database
    if test_id in DEFAULT_TESTS:
        return DEFAULT_TESTS[test_id]['questions']
    return []


def get_all_tests():
    """Get all tests from database, falling back to defaults if not seeded."""
    try:
        db = get_sqlite_db()
        cursor = db.execute('SELECT * FROM tests')
        tests = {}
        for row in cursor.fetchall():
            tests[row['test_id']] = {
                'name': row['name'],
                'chapter': row['chapter'],
                'passing_score': row['passing_score'],
                'questions': json.loads(row['questions']) if row['questions'] else []
            }
        if tests:
            return tests
    except Exception as e:
        print(f"Error loading tests from SQLite: {e}")
    # Fallback to default tests
    return DEFAULT_TESTS


def get_test(test_id):
    """Get a single test by ID."""
    tests = get_all_tests()
    return tests.get(test_id)


def save_test(test_id, test_data):
    """Save a test to database."""
    db = get_sqlite_db()
    db.execute('''
        INSERT OR REPLACE INTO tests (test_id, name, chapter, passing_score, questions)
        VALUES (?, ?, ?, ?, ?)
    ''', (test_id, test_data['name'], test_data['chapter'],
          test_data['passing_score'], json.dumps(test_data['questions'])))
    db.commit()


def seed_tests_to_database():
    """Seed all default tests to database."""
    for test_id, test_data in DEFAULT_TESTS.items():
        save_test(test_id, test_data)
    return len(DEFAULT_TESTS)


# Initialize database on startup (with error handling for paused databases)
def safe_init_db():
    """Try to initialize database, but don't crash if unavailable."""
    try:
        init_db()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Warning: Database initialization failed: {e}")
        print("App will start but database features may not work until DB is available")

safe_init_db()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def proctor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        role = session.get('role', '')
        if not (has_role(role, 'proctor') or has_role(role, 'admin')):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if not has_role(session.get('role', ''), 'admin'):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def jwg_required(f):
    """Decorator for JWG (Judges Working Group) members only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        role = session.get('role', '')
        if not (has_role(role, 'jwg') or has_role(role, 'admin')):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def get_proctor_tests(username, include_general=True):
    """Get tests available for a proctor based on assigned categories and per-category levels."""
    user = get_user(username)
    all_tests = get_all_tests()
    if not user:
        return {}
    if has_role(user, 'admin'):
        return all_tests  # Admin sees all tests

    available_tests = {}

    # Include general test only if requested (for results viewing, not answer keys)
    if include_general and GENERAL_TEST_ID in all_tests:
        available_tests[GENERAL_TEST_ID] = all_tests[GENERAL_TEST_ID]

    # Categories format: {cat_id: {"level": "national", "expiration": "2025-12-31"}}
    categories = user.get('categories', {})

    # Add category-specific tests filtered by per-category level
    for cat_id, cat_data in categories.items():
        if cat_id in CATEGORIES and isinstance(cat_data, dict):
            cat_level = cat_data.get('level', 'regional')
            for test_id in CATEGORIES[cat_id]['tests']:
                if cat_level == 'regional' and '_regional' in test_id:
                    if test_id in all_tests:
                        available_tests[test_id] = all_tests[test_id]
                elif cat_level in ['national', 'examiner']:
                    # National and Examiner levels can administer both regional and national tests
                    if test_id in all_tests:
                        available_tests[test_id] = all_tests[test_id]
    return available_tests


def get_proctor_results(username):
    """Get test results for tests in proctor's assigned categories."""
    available_tests = get_proctor_tests(username)
    all_results = get_all_test_results()
    return {rid: r for rid, r in all_results.items() if r['test_id'] in available_tests}


@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))

    role = session.get('role', '')
    if has_role(role, 'admin'):
        return redirect(url_for('admin_dashboard'))
    elif has_role(role, 'proctor'):
        return redirect(url_for('proctor_dashboard'))
    elif has_role(role, 'jwg'):
        return redirect(url_for('jwg_dashboard'))

    # Get student's assigned tests
    user = get_user(session.get('user'))
    assigned_tests = user.get('assigned_tests', []) if user else []

    return render_template('index.html',
                         user=session.get('user'),
                         role=session.get('role'),
                         name=session.get('name'),
                         tests=get_all_tests(),
                         assigned_tests=assigned_tests)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').lower()
        password = request.form.get('password', '')

        user = get_user(username)
        if user and user['password'] == password:
            session['user'] = username
            session['role'] = user['role']
            session['name'] = user['name']
            # Record last login
            db = get_sqlite_db()
            db.execute('UPDATE users SET last_login = ? WHERE username = ?',
                       (datetime.now().isoformat(), username))
            db.commit()
            return redirect(url_for('index'))
        else:
            error = 'Invalid username or password'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/test/<test_id>')
@login_required
def take_test(test_id):
    if not has_role(session.get('role', ''), 'student'):
        return redirect(url_for('index'))

    all_tests = get_all_tests()
    if test_id not in all_tests:
        return "Test not found", 404

    # Check if student is assigned this test
    user = get_user(session.get('user'))
    assigned_tests = user.get('assigned_tests', []) if user else []
    if test_id not in assigned_tests:
        return "You are not assigned to this test", 403

    test = all_tests[test_id]
    questions = get_test_questions(test_id)
    return render_template('test.html',
                         questions=questions,
                         total=len(questions),
                         test_id=test_id,
                         test_name=test['name'],
                         passing_score=test['passing_score'])


@app.route('/submit-test/<test_id>', methods=['POST'])
@login_required
def submit_test(test_id):
    if not has_role(session.get('role', ''), 'student'):
        return jsonify({'error': 'Unauthorized'}), 403

    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    test = all_tests[test_id]
    questions = get_test_questions(test_id)
    passing_score = test['passing_score']

    data = request.json
    answers = data.get('answers', {})
    sections = data.get('sections', {})

    # Grade the test
    total_points = 0
    results = []

    for q in questions:
        q_id = str(q['id'])
        user_answer = answers.get(q_id)
        user_section_raw = sections.get(q_id, '')
        user_section_normalized = normalize_section_ref(user_section_raw)
        correct_section_normalized = normalize_section_ref(q['correct_section'])

        is_correct = user_answer == q['correct']
        is_section_correct = user_section_normalized == correct_section_normalized

        # Calculate points for this question
        # MC correct = 3.5 pts, Reference correct = 0.5 pts (max 4 pts per question)
        question_points = 0
        if is_correct:
            question_points += 3.5  # Points for correct MC answer
        if is_section_correct:
            question_points += 0.5  # Points for correct reference

        total_points += question_points

        results.append({
            'id': q['id'],
            'question': q['question'],
            'user_answer': user_answer,
            'correct_answer': q['correct'],
            'is_correct': is_correct,
            'user_section': sections.get(q_id, ''),
            'correct_section': q['correct_section'],
            'is_section_correct': is_section_correct,
            'question_points': question_points,
            'options': q['options']
        })

    total_possible = len(questions) * 4  # 100 points
    score = round((total_points / total_possible) * 100, 1)
    passed = score >= passing_score

    # Store result in database
    result_id = str(uuid.uuid4())[:8]
    save_test_result(result_id, {
        'student': session.get('name'),
        'username': session.get('user'),
        'test_id': test_id,
        'test_name': test['name'],
        'score': score,
        'total_points': total_points,
        'total_possible': total_possible,
        'total_questions': len(questions),
        'passing_score': passing_score,
        'passed': passed,
        'timestamp': datetime.now().isoformat(),
        'results': results
    })

    return jsonify({
        'result_id': result_id,
        'score': score,
        'total_points': total_points,
        'total_possible': total_possible,
        'passing_score': passing_score,
        'passed': passed
    })


@app.route('/results/<result_id>')
@login_required
def view_results(result_id):
    result = get_test_result(result_id)
    if not result:
        return "Results not found", 404

    role = session.get('role', '')

    # Students can only view their own results
    if has_role(role, 'student') and not has_role(role, 'proctor') and not has_role(role, 'admin') and result['username'] != session.get('user'):
        return "Unauthorized", 403

    # Proctors can only view results for their assigned categories
    if has_role(role, 'proctor') and not has_role(role, 'admin'):
        available_tests = get_proctor_tests(session.get('user'))
        if result['test_id'] not in available_tests:
            return "Unauthorized", 403

    # Check if examiner can approve references (non-passing tests only)
    can_approve = (has_role(role, 'proctor') or has_role(role, 'admin')) and not result.get('passed', True)

    return render_template('results.html', result=result, result_id=result_id,
                         can_approve=can_approve, role=role)


@app.route('/approve-reference/<result_id>', methods=['POST'])
@proctor_required
def approve_reference(result_id):
    """Allow examiner to approve a reference answer on a non-passing test."""
    result = get_test_result(result_id)
    if not result:
        return jsonify({'error': 'Results not found'}), 404

    # Only allow on non-passing tests
    if result.get('passed', True):
        return jsonify({'error': 'Can only approve references on non-passing tests'}), 400

    # Check proctor has access to this test
    role = session.get('role', '')
    if has_role(role, 'proctor') and not has_role(role, 'admin'):
        available_tests = get_proctor_tests(session.get('user'))
        if result['test_id'] not in available_tests:
            return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    question_id = data.get('question_id')

    if question_id is None:
        return jsonify({'error': 'Question ID required'}), 400

    # Find and update the question result
    updated = False
    for r in result.get('results', []):
        if r['id'] == question_id and not r.get('is_section_correct'):
            r['is_section_correct'] = True
            r['section_approved_by'] = session.get('user')
            r['question_points'] = r.get('question_points', 0) + 0.5
            updated = True
            break

    if not updated:
        return jsonify({'error': 'Question not found or already approved'}), 400

    # Recalculate total score
    total_points = sum(r.get('question_points', 0) for r in result['results'])
    total_possible = result.get('total_possible', len(result['results']) * 4)
    new_score = round((total_points / total_possible) * 100, 1)
    passing_score = result.get('passing_score', 70)

    result['total_points'] = total_points
    result['score'] = new_score
    result['passed'] = new_score >= passing_score

    # Save updated result
    save_test_result(result_id, result)

    return jsonify({
        'success': True,
        'new_score': new_score,
        'total_points': total_points,
        'passed': result['passed'],
        'message': f'Reference approved. New score: {new_score}%'
    })


@app.route('/proctor')
@proctor_required
def proctor_dashboard():
    username = session.get('user')
    available_tests = get_proctor_tests(username)
    available_results = get_proctor_results(username)

    # Get assigned categories for display (2-letter abbreviations)
    user = get_user(username) or {}
    assigned_categories = user.get('categories', [])
    category_names = [c.upper() for c in assigned_categories if c in CATEGORIES]

    # Get all students
    all_users = get_all_users()
    students = {u: data for u, data in all_users.items() if has_role(data, 'student')}

    # Add test status to each student
    all_results = get_all_test_results()
    for student_username, student_data in students.items():
        # Build test_results dict with most recent result for each test
        test_results = {}
        for result_id, result in all_results.items():
            if result.get('username') == student_username:
                test_id = result.get('test_id')
                # Keep the most recent result for each test
                if test_id not in test_results or result.get('timestamp', '') > test_results[test_id].get('timestamp', ''):
                    test_results[test_id] = {
                        'score': result.get('score'),
                        'passed': result.get('passed'),
                        'chapter': available_tests.get(test_id, {}).get('chapter', ''),
                        'result_id': result_id,
                        'timestamp': result.get('timestamp', '')
                    }
        student_data['test_results'] = test_results
        student_data['tests_completed'] = len(test_results)
        student_data['tests_assigned'] = len(student_data.get('assigned_tests', []))

    return render_template('proctor.html',
                         results=available_results,
                         tests=available_tests,
                         students=students,
                         categories=category_names,
                         is_admin=has_role(session.get('role', ''), 'admin'))


@app.route('/answer-key/<test_id>')
@proctor_required
def answer_key(test_id):
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return "Test not found", 404

    # Check if proctor has access to this test
    username = session.get('user')
    available_tests = get_proctor_tests(username)

    if test_id not in available_tests:
        return "Unauthorized", 403

    test = all_tests[test_id]
    questions = get_test_questions(test_id)
    return render_template('answer_key.html',
                         questions=questions,
                         test_name=test['name'],
                         test_id=test_id,
                         tests=available_tests)


@app.route('/edit-test/<test_id>')
@proctor_required
def edit_test(test_id):
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return "Test not found", 404

    # Check if proctor has access to this test
    username = session.get('user')
    available_tests = get_proctor_tests(username)

    if test_id not in available_tests:
        return "Unauthorized", 403

    test = all_tests[test_id]
    questions = get_test_questions(test_id)
    return render_template('edit_test.html',
                         questions=questions,
                         test_name=test['name'],
                         test_id=test_id,
                         tests=available_tests)


@app.route('/save-test/<test_id>', methods=['POST'])
@proctor_required
def save_test_questions(test_id):
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    # Check if proctor has access to this test
    username = session.get('user')
    available_tests = get_proctor_tests(username)

    if test_id not in available_tests:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    questions = data.get('questions', [])

    # Validate exactly 25 questions
    if len(questions) != 25:
        return jsonify({'error': f'Test must have exactly 25 questions. Received {len(questions)}.'}), 400

    # Validate questions
    for q in questions:
        if not q.get('question') or not q.get('options') or len(q['options']) != 4:
            return jsonify({'error': 'Each question must have text and 4 options'}), 400
        if q.get('correct') not in [0, 1, 2, 3]:
            return jsonify({'error': 'Each question must have a valid correct answer (0-3)'}), 400

    # Update the test in the database
    test = all_tests[test_id]
    test['questions'] = questions
    save_test(test_id, test)
    return jsonify({'success': True, 'message': f'Test saved with {len(questions)} questions'})


@app.route('/reset-test/<test_id>', methods=['POST'])
@proctor_required
def reset_test(test_id):
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    # Check if proctor has access to this test
    username = session.get('user')
    available_tests = get_proctor_tests(username)

    if test_id not in available_tests:
        return jsonify({'error': 'Unauthorized'}), 403

    # Reset to default questions from questions.py
    if test_id not in DEFAULT_TESTS:
        return jsonify({'error': 'No default questions available for this test'}), 404

    default_test = DEFAULT_TESTS[test_id]
    save_test(test_id, default_test)

    return jsonify({'success': True, 'message': 'Test reset to default questions'})


@app.route('/proctor/add-student', methods=['POST'])
@proctor_required
def add_student():
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password', '')
    name = data.get('name', '')
    assigned_tests = data.get('assigned_tests', [])

    if not username or not password or not name:
        return jsonify({'error': 'All fields required'}), 400

    if get_user(username):
        return jsonify({'error': 'Username already exists'}), 400

    save_user(username, {
        'password': password,
        'role': 'student',
        'name': name,
        'categories': [],
        'assigned_tests': assigned_tests
    })

    return jsonify({'success': True, 'message': f'Student {name} added with {len(assigned_tests)} test(s)'})


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Allow proctors and admins to change their own password."""
    role = session.get('role', '')
    if not (has_role(role, 'proctor') or has_role(role, 'admin')):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')

    username = session.get('user')

    if not current_password or not new_password or not confirm_password:
        return jsonify({'error': 'All fields required'}), 400

    user = get_user(username)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if user['password'] != current_password:
        return jsonify({'error': 'Current password is incorrect'}), 400

    if new_password != confirm_password:
        return jsonify({'error': 'New passwords do not match'}), 400

    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    user['password'] = new_password
    save_user(username, user)
    return jsonify({'success': True, 'message': 'Password changed successfully'})


# Admin routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    # Get all proctors, students, and JWG members
    all_users = get_all_users()
    proctors = {u: data for u, data in all_users.items() if has_role(data, 'proctor')}
    students = {u: data for u, data in all_users.items() if has_role(data, 'student')}
    jwg_members = {u: data for u, data in all_users.items() if has_role(data, 'jwg')}
    all_results = get_all_test_results()
    all_tests = get_all_tests()

    # Attach test results to each student
    for username, student in students.items():
        student_results = {}
        for result_id, result in all_results.items():
            if result.get('username') == username:
                test_id = result.get('test_id')
                # Keep the most recent result for each test
                if test_id not in student_results or result.get('timestamp', '') > student_results[test_id].get('timestamp', ''):
                    student_results[test_id] = {
                        'score': result.get('score'),
                        'passed': result.get('passed'),
                        'chapter': all_tests.get(test_id, {}).get('chapter', ''),
                        'result_id': result_id
                    }
        student['test_results'] = student_results

    # Separate examiners (E-level) from trainers (N/R-level)
    examiners = {}
    trainers = {}
    for username, proctor in proctors.items():
        cats = proctor.get('categories', {})
        if isinstance(cats, dict):
            levels = [cat_data.get('level', '') for cat_data in cats.values() if isinstance(cat_data, dict)]
            if 'examiner' in levels:
                examiners[username] = proctor
            if 'regional' in levels or 'national' in levels:
                trainers[username] = proctor

    # Check if tests need seeding (compare DB tests with defaults)
    needs_seeding = False
    for test_id, default_test in DEFAULT_TESTS.items():
        db_test = all_tests.get(test_id)
        if not db_test:
            needs_seeding = True
            break
        # Compare question count or content hash
        db_questions = db_test.get('questions', [])
        default_questions = default_test.get('questions', [])
        if len(db_questions) != len(default_questions):
            needs_seeding = True
            break
        # Simple check: compare first question text if exists
        if db_questions and default_questions:
            if db_questions[0].get('question') != default_questions[0].get('question'):
                needs_seeding = True
                break

    # Check if any examiners need category migration
    needs_migration = False
    for username, user_data in all_users.items():
        if not has_role(user_data, 'proctor'):
            continue
        categories = user_data.get('categories', {})
        if isinstance(categories, list):
            needs_migration = True
            break
        elif isinstance(categories, dict):
            for cat_id, cat_data in categories.items():
                if not isinstance(cat_data, dict):
                    needs_migration = True
                    break
            if needs_migration:
                break

    return render_template('admin.html',
                         proctors=proctors,
                         examiners=examiners,
                         trainers=trainers,
                         students=students,
                         jwg_members=jwg_members,
                         categories=CATEGORIES,
                         results=all_results,
                         tests=all_tests,
                         needs_seeding=needs_seeding,
                         needs_migration=needs_migration,
                         is_jwg=has_role(session.get('role', ''), 'jwg'))


@app.route('/admin/add-proctor', methods=['POST'])
@admin_required
def add_proctor():
    data = request.json
    username = data.get('username', '').lower()
    name = data.get('name', '')
    categories = data.get('categories', {})  # Format: {cat_id: {"level": "...", "expiration": "..."}}

    if not username or not name:
        return jsonify({'error': 'Username and name are required'}), 400

    # Validate categories - format: {cat_id: {"level": "...", "expiration": "..."}}
    valid_categories = {}
    if isinstance(categories, dict):
        for cat_id, cat_data in categories.items():
            if cat_id in CATEGORIES and isinstance(cat_data, dict):
                level = cat_data.get('level', 'regional')
                if level in PROCTOR_LEVELS:
                    valid_categories[cat_id] = {
                        'level': level,
                        'expiration': cat_data.get('expiration', '')
                    }

    existing = get_user(username)
    if existing:
        existing_cats = existing.get('categories', {})
        if isinstance(existing_cats, dict):
            existing_cats.update(valid_categories)
        else:
            existing_cats = valid_categories
        existing['role'] = add_role(existing['role'], 'proctor')
        existing['name'] = name
        existing['categories'] = existing_cats
        save_user(username, existing)
        cat_count = len(valid_categories)
        return jsonify({'success': True, 'message': f'Existing user {name} updated with examiner role and {cat_count} category rating(s)'})

    save_user(username, {
        'password': 'password',
        'role': 'proctor',
        'name': name,
        'categories': valid_categories
    })

    cat_count = len(valid_categories)
    return jsonify({'success': True, 'message': f'Examiner {name} added with {cat_count} category rating(s)'})


@app.route('/admin/update-proctor/<username>', methods=['POST'])
@admin_required
def update_proctor(username):
    user = get_user(username)
    if not user or not has_role(user, 'proctor'):
        return jsonify({'error': 'Examiner not found'}), 404

    data = request.json
    categories = data.get('categories', {})

    # Validate categories - format: {cat_id: {"level": "...", "expiration": "..."}}
    if isinstance(categories, dict):
        valid_categories = {}
        for cat_id, cat_data in categories.items():
            if cat_id in CATEGORIES and isinstance(cat_data, dict):
                level = cat_data.get('level', 'regional')
                if level in PROCTOR_LEVELS:
                    valid_categories[cat_id] = {
                        'level': level,
                        'expiration': cat_data.get('expiration', '')
                    }
        user['categories'] = valid_categories

    # Update password if provided
    if data.get('password'):
        user['password'] = data['password']

    # Update name if provided
    if data.get('name'):
        user['name'] = data['name']

    save_user(username, user)
    return jsonify({'success': True, 'message': 'Examiner updated'})


@app.route('/admin/delete-proctor/<username>', methods=['POST'])
@admin_required
def delete_proctor(username):
    user = get_user(username)
    if not user or not has_role(user, 'proctor'):
        return jsonify({'error': 'Proctor not found'}), 404

    new_role = remove_role(user['role'], 'proctor')
    if new_role:
        user['role'] = new_role
        user['categories'] = {}
        save_user(username, user)
    else:
        delete_user(username)
    return jsonify({'success': True, 'message': 'Proctor deleted'})


@app.route('/admin/add-student', methods=['POST'])
@admin_required
def admin_add_student():
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password', '')
    name = data.get('name', '')
    assigned_tests = data.get('assigned_tests', [])

    if not username or not password or not name:
        return jsonify({'error': 'All fields required'}), 400

    existing = get_user(username)
    if existing:
        existing['role'] = add_role(existing['role'], 'student')
        existing['name'] = name
        existing['assigned_tests'] = assigned_tests
        save_user(username, existing)
        return jsonify({'success': True, 'message': f'Existing user {name} updated with candidate role and {len(assigned_tests)} test(s)'})

    save_user(username, {
        'password': password,
        'role': 'student',
        'name': name,
        'categories': [],
        'assigned_tests': assigned_tests
    })

    return jsonify({'success': True, 'message': f'Student {name} added with {len(assigned_tests)} test(s)'})


@app.route('/admin/delete-student/<username>', methods=['POST'])
@admin_required
def admin_delete_student(username):
    user = get_user(username)
    if not user or not has_role(user, 'student'):
        return jsonify({'error': 'Student not found'}), 404

    new_role = remove_role(user['role'], 'student')
    if new_role:
        user['role'] = new_role
        user['assigned_tests'] = []
        save_user(username, user)
    else:
        delete_user(username)
    return jsonify({'success': True, 'message': 'Student deleted'})


@app.route('/admin/get-proctor/<username>')
@admin_required
def get_proctor_route(username):
    user = get_user(username)
    if not user or not has_role(user, 'proctor'):
        return jsonify({'error': 'Examiner not found'}), 404

    return jsonify({
        'username': username,
        'name': user['name'],
        'categories': user.get('categories', {})
    })


@app.route('/admin/seed-tests', methods=['POST'])
@admin_required
def admin_seed_tests():
    """Seed database with default test questions from questions.py."""
    try:
        count = seed_tests_to_database()
        return jsonify({'success': True, 'message': f'Seeded {count} tests to database'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/get-tests')
@admin_required
def admin_get_tests():
    """Get all tests for admin management."""
    tests = get_all_tests()
    return jsonify({
        'tests': [{'id': tid, 'name': t['name'], 'chapter': t['chapter'],
                   'passing_score': t['passing_score'], 'question_count': len(t.get('questions', []))}
                  for tid, t in tests.items()],
        'source': 'local'
    })


@app.route('/admin/migrate-categories', methods=['POST'])
@admin_required
def migrate_categories():
    """One-time migration to convert old category format to new format with per-category expiration."""
    all_users = get_all_users()
    migrated = 0

    for username, user_data in all_users.items():
        if not has_role(user_data, 'proctor'):
            continue

        categories = user_data.get('categories', {})
        needs_migration = False
        new_categories = {}

        if isinstance(categories, list):
            # Legacy list format
            level = user_data.get('proctor_level', 'regional')
            for cat_id in categories:
                if cat_id in CATEGORIES:
                    new_categories[cat_id] = {'level': level, 'expiration': ''}
            needs_migration = True
        elif isinstance(categories, dict):
            for cat_id, cat_data in categories.items():
                if isinstance(cat_data, dict):
                    # Already new format
                    new_categories[cat_id] = cat_data
                elif cat_data in PROCTOR_LEVELS:
                    # Old format - just level string
                    new_categories[cat_id] = {'level': cat_data, 'expiration': ''}
                    needs_migration = True

        if needs_migration:
            user_data['categories'] = new_categories
            save_user(username, user_data)
            migrated += 1

    return jsonify({'success': True, 'message': f'Migrated {migrated} examiner(s) to new format'})


# JWG (Judges Working Group) routes
@app.route('/jwg')
@jwg_required
def jwg_dashboard():
    """Dashboard for Judges Working Group members to verify question references."""
    all_tests = get_all_tests()
    verifications = get_question_verifications()
    flags = get_question_flags()

    # Calculate verification and flag stats per test
    total_flagged = 0
    test_stats = {}
    for test_id, test_data in all_tests.items():
        questions = test_data.get('questions', [])
        total = len(questions)
        verified = sum(1 for q in questions if f"{test_id}_{q['id']}" in verifications)
        flag_count = sum(1 for q in questions if f"{test_id}_{q['id']}" in flags)
        total_flagged += flag_count
        test_stats[test_id] = {
            'name': test_data['name'],
            'chapter': test_data['chapter'],
            'total': total,
            'verified': verified,
            'percent': round((verified / total * 100) if total > 0 else 0, 1),
            'flagged': flag_count
        }

    return render_template('jwg.html',
                         test_stats=test_stats,
                         categories=CATEGORIES,
                         user=session.get('user'),
                         name=session.get('name'),
                         is_admin=has_role(session.get('role', ''), 'admin'),
                         total_flagged=total_flagged)


@app.route('/jwg/verify/<test_id>')
@jwg_required
def jwg_verify_test(test_id):
    """View questions for a specific test to verify references."""
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return "Test not found", 404

    test = all_tests[test_id]
    questions = test.get('questions', [])
    verifications = get_question_verifications(test_id)
    flags = get_question_flags(test_id)

    # Add verification and flag status to each question
    for q in questions:
        key = f"{test_id}_{q['id']}"
        if key in verifications:
            q['verified'] = True
            q['verified_by'] = verifications[key]['verifier_name']
            q['verified_at'] = verifications[key]['verified_at']
        else:
            q['verified'] = False
        if key in flags:
            q['flagged'] = True
            q['flagged_by'] = flags[key]['flagger_name']
        else:
            q['flagged'] = False

    # Sort questions by SCM reference (e.g., "8.1.3.1" before "8.1.5.3")
    def section_sort_key(q):
        ref = q.get('correct_section', '')
        ref = re.sub(r'[–—−]', '-', ref)
        parts = re.split(r'[.\-\s]+', ref.strip())
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(p)
        return result

    questions.sort(key=section_sort_key)

    return render_template('jwg_verify.html',
                         test_id=test_id,
                         test_name=test['name'],
                         questions=questions,
                         user=session.get('user'),
                         name=session.get('name'),
                         is_admin=has_role(session.get('role', ''), 'admin'))


@app.route('/jwg/verify-question', methods=['POST'])
@jwg_required
def jwg_verify_question():
    """API endpoint to verify or unverify a question reference."""
    data = request.json
    test_id = data.get('test_id')
    question_id = data.get('question_id')
    action = data.get('action', 'verify')  # 'verify' or 'unverify'

    if not test_id or question_id is None:
        return jsonify({'error': 'test_id and question_id are required'}), 400

    # Verify the test and question exist
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    questions = all_tests[test_id].get('questions', [])
    question_exists = any(q['id'] == question_id for q in questions)
    if not question_exists:
        return jsonify({'error': 'Question not found'}), 404

    username = session.get('user')
    name = session.get('name')

    if action == 'verify':
        success = save_question_verification(test_id, question_id, username, name)
        if success:
            return jsonify({
                'success': True,
                'message': 'Question verified',
                'verified_by': name,
                'verified_at': datetime.now().isoformat()
            })
        else:
            return jsonify({'error': 'Failed to save verification'}), 500
    elif action == 'unverify':
        success = remove_question_verification(test_id, question_id)
        if success:
            return jsonify({'success': True, 'message': 'Verification removed'})
        else:
            return jsonify({'error': 'Failed to remove verification'}), 500
    else:
        return jsonify({'error': 'Invalid action'}), 400


@app.route('/jwg/update-question', methods=['POST'])
@jwg_required
def jwg_update_question():
    """API endpoint for JWG to update a question's text or reference."""
    data = request.json
    test_id = data.get('test_id')
    question_id = data.get('question_id')
    new_question = data.get('question')
    new_reference = data.get('correct_section')
    new_correct = data.get('correct')  # Index of correct answer
    new_options = data.get('options')  # List of 4 options

    if not test_id or question_id is None:
        return jsonify({'error': 'test_id and question_id are required'}), 400

    # Get the test
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    test = all_tests[test_id]
    questions = test.get('questions', [])

    # Find the question and compute diff
    target_q = None
    for q in questions:
        if q['id'] == question_id:
            target_q = q
            break

    if not target_q:
        return jsonify({'error': 'Question not found'}), 404

    # Compute changes (old vs new)
    changes = {}
    if new_question and new_question != target_q.get('question'):
        changes['question'] = {'old': target_q['question'], 'new': new_question}
        target_q['question'] = new_question
    if new_reference and new_reference != target_q.get('correct_section'):
        changes['correct_section'] = {'old': target_q.get('correct_section', ''), 'new': new_reference}
        target_q['correct_section'] = new_reference
    if new_correct is not None and new_correct != target_q.get('correct'):
        changes['correct'] = {'old': target_q['correct'], 'new': new_correct}
        target_q['correct'] = new_correct
    if new_options and len(new_options) == 4 and new_options != target_q.get('options'):
        old_options = target_q.get('options', [])
        option_changes = {}
        for i, (old_opt, new_opt) in enumerate(zip(old_options, new_options)):
            if old_opt != new_opt:
                option_changes[str(i)] = {'old': old_opt, 'new': new_opt}
        if option_changes:
            changes['options'] = option_changes
        target_q['options'] = new_options

    if not changes:
        return jsonify({'success': True, 'message': 'No changes detected'})

    # Save the updated test
    test['questions'] = questions
    save_test(test_id, test)

    # Record the change for audit trail
    username = session.get('user')
    name = session.get('name')
    save_question_change(test_id, question_id, username, name, changes)
    print(f"JWG Update: {name} ({username}) updated question {question_id} in {test_id}")

    return jsonify({
        'success': True,
        'message': 'Question updated successfully',
        'updated_by': name
    })


@app.route('/jwg/question-history')
@jwg_required
def jwg_question_history():
    """API endpoint to get change history for a question."""
    test_id = request.args.get('test_id')
    question_id = request.args.get('question_id', type=int)

    if not test_id or question_id is None:
        return jsonify({'error': 'test_id and question_id are required'}), 400

    changes = get_question_changes(test_id, question_id)
    return jsonify({'success': True, 'changes': changes})


@app.route('/jwg/flag-question', methods=['POST'])
@jwg_required
def jwg_flag_question():
    """API endpoint to flag or unflag a question."""
    data = request.json
    test_id = data.get('test_id')
    question_id = data.get('question_id')
    action = data.get('action', 'flag')  # 'flag' or 'unflag'

    if not test_id or question_id is None:
        return jsonify({'error': 'test_id and question_id are required'}), 400

    # Verify the test and question exist
    all_tests = get_all_tests()
    if test_id not in all_tests:
        return jsonify({'error': 'Test not found'}), 404

    questions = all_tests[test_id].get('questions', [])
    question_exists = any(q['id'] == question_id for q in questions)
    if not question_exists:
        return jsonify({'error': 'Question not found'}), 404

    username = session.get('user')
    name = session.get('name')

    if action == 'flag':
        save_question_flag(test_id, question_id, username, name)
        return jsonify({
            'success': True,
            'message': 'Question flagged',
            'flagged_by': name
        })
    elif action == 'unflag':
        remove_question_flag(test_id, question_id)
        return jsonify({'success': True, 'message': 'Flag removed'})
    else:
        return jsonify({'error': 'Invalid action'}), 400


@app.route('/jwg/flagged-report')
@jwg_required
def jwg_flagged_report():
    """Report page showing all flagged questions grouped by chapter."""
    all_tests = get_all_tests()
    flags = get_question_flags()

    # Build flagged questions list grouped by chapter
    flagged_by_chapter = {}
    for key, flag_data in flags.items():
        test_id, question_id_str = key.rsplit('_', 1)
        question_id = int(question_id_str)

        if test_id not in all_tests:
            continue

        test = all_tests[test_id]
        questions = test.get('questions', [])
        question = next((q for q in questions if q['id'] == question_id), None)
        if not question:
            continue

        chapter = test.get('chapter', 'Unknown')
        if chapter not in flagged_by_chapter:
            flagged_by_chapter[chapter] = []

        flagged_by_chapter[chapter].append({
            'test_id': test_id,
            'test_name': test['name'],
            'question_id': question_id,
            'question': question.get('question', ''),
            'options': question.get('options', []),
            'correct': question.get('correct', 0),
            'correct_section': question.get('correct_section', ''),
            'flagged_by': flag_data['flagger_name'],
            'flagged_at': flag_data['flagged_at']
        })

    # Sort chapters
    sorted_chapters = dict(sorted(flagged_by_chapter.items()))

    total_flagged = sum(len(items) for items in sorted_chapters.values())

    return render_template('jwg_flagged_report.html',
                         flagged_by_chapter=sorted_chapters,
                         total_flagged=total_flagged,
                         user=session.get('user'),
                         name=session.get('name'),
                         is_admin=has_role(session.get('role', ''), 'admin'))


@app.route('/admin/add-jwg', methods=['POST'])
@admin_required
def admin_add_jwg():
    """Add a new JWG member."""
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password', 'password')
    name = data.get('name', '')

    if not username or not name:
        return jsonify({'error': 'Username and name are required'}), 400

    existing = get_user(username)
    if existing:
        existing['role'] = add_role(existing['role'], 'jwg')
        existing['name'] = name
        save_user(username, existing)
        password = existing['password']
        message = f'Existing user {name} updated with JWG role'
    else:
        save_user(username, {
            'password': password,
            'role': 'jwg',
            'name': name,
            'categories': []
        })
        message = f'JWG member {name} added'
    if data.get('send_email'):
        success, email_msg = send_login_email(username, name, username, password, 'JWG member')
        if success:
            message += ' and login email sent'
        else:
            message += f'. Email failed: {email_msg}'

    return jsonify({'success': True, 'message': message})


@app.route('/admin/delete-jwg/<username>', methods=['POST'])
@admin_required
def admin_delete_jwg(username):
    """Delete a JWG member."""
    user = get_user(username)
    if not user or not has_role(user, 'jwg'):
        return jsonify({'error': 'JWG member not found'}), 404

    new_role = remove_role(user['role'], 'jwg')
    if new_role:
        user['role'] = new_role
        save_user(username, user)
    else:
        delete_user(username)
    return jsonify({'success': True, 'message': 'JWG member deleted'})


@app.route('/admin/resend-email', methods=['POST'])
@admin_required
def resend_email():
    """Resend login email to a user."""
    data = request.json
    username = data.get('username', '')
    user = get_user(username)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    success, msg = send_login_email(username, user['name'], username, user['password'])
    if success:
        return jsonify({'success': True, 'message': f'Login email sent to {username}'})
    else:
        return jsonify({'error': f'Email failed: {msg}'}), 500


# ==================== REAL-TIME SCORING SYSTEM ====================

import random
import string
import sys

# --- Constants ---

PERMANENT_ROOMS = {
    'FS1': {'name': 'Formation Skydiving Panel 1', 'scoring_type': 'fs-points', 'panel_size': 5},
    'FS2': {'name': 'Formation Skydiving Panel 2', 'scoring_type': 'fs-points', 'panel_size': 5},
    'CP1': {'name': 'Canopy Piloting', 'scoring_type': 'cp-freestyle', 'panel_size': 5},
    'WS1': {'name': 'Wingsuit', 'scoring_type': 'ws-free', 'panel_size': 3, 'allowed_types': ['ws-free', 'ws-compulsory']},
    'CF1': {'name': 'Canopy Formation Panel 1', 'scoring_type': 'cf-points', 'panel_size': 5},
    'CF2': {'name': 'Canopy Formation Panel 2', 'scoring_type': 'cf-points', 'panel_size': 5},
    'AE1': {'name': 'Artistic Events', 'scoring_type': 'ae-score', 'panel_size': 5},
}

WS_SCORE_FIELDS = {
    'ws-free': {
        'style': (0, 10),
        'dive_plan': (0, 10),
        'cam_quality': (0, 7),
        'cam_progressive': (0, 3),
    },
    'ws-compulsory': {
        'style': (0, 10),
    },
    'cp-freestyle': {
        'score': (0, 10),
    },
    'fs-points': {
        'points': (0, 99),
    },
    'cf-points': {
        'points': (0, 99),
    },
    'ae-score': {
        'score': (0, 10),
    },
}

PANEL_SIZES = {
    'ws-free': 3, 'ws-compulsory': 3,
    'cp-freestyle': 5, 'fs-points': 5, 'cf-points': 5, 'ae-score': 5,
}

# --- Redis setup ---

import redis as redis_lib

REDIS_URL = os.environ.get('REDIS_URL', '')
REDIS_AVAILABLE = False
redis_client = None

if REDIS_URL:
    try:
        redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        REDIS_AVAILABLE = True
        print("[REDIS] Connected")
    except Exception as e:
        redis_client = None
        print(f"[REDIS] Connection failed ({e}), using in-memory fallback")

# --- SocketIO setup ---

try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    _async_mode = 'gevent' if 'gunicorn' in sys.modules else 'threading'
    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode=_async_mode,
        message_queue=REDIS_URL if REDIS_AVAILABLE else None,
        ping_timeout=60,
        ping_interval=25,
        logger=False,
        engineio_logger=False,
    )
    SOCKETIO_ENABLED = True
    print(f"[SOCKETIO] Enabled (async_mode={_async_mode}, redis={'yes' if REDIS_AVAILABLE else 'no'})")
except ImportError:
    SOCKETIO_ENABLED = False
    socketio = None
    print("[SOCKETIO] Disabled (flask-socketio not installed)")

# --- Room storage with Redis + in-memory fallback ---

WS_ROOMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ws_scoring_rooms.json')
_ws_rooms_memory = {}


def _save_ws_rooms_to_file():
    """Persist in-memory rooms to disk (fallback mode)."""
    try:
        saveable = {}
        for code, room in _ws_rooms_memory.items():
            r = dict(room)
            r['judges'] = {str(k): {'name': v.get('name', ''), 'connected': False}
                           for k, v in r.get('judges', {}).items()}
            r.pop('video', None)
            # Convert int keys in scores to strings for JSON
            r['scores'] = {str(k): v for k, v in r.get('scores', {}).items()}
            saveable[code] = r
        with open(WS_ROOMS_FILE, 'w') as f:
            json.dump(saveable, f)
    except Exception as e:
        print(f"[WS_ROOMS] Error saving rooms to file: {e}")


def _load_ws_rooms_from_file():
    """Load rooms from disk (fallback mode)."""
    try:
        if os.path.exists(WS_ROOMS_FILE):
            with open(WS_ROOMS_FILE, 'r') as f:
                rooms = json.load(f)
            for code, room in rooms.items():
                room['scores'] = {int(k): v for k, v in room.get('scores', {}).items()}
                room['judges'] = {int(k): v for k, v in room.get('judges', {}).items()}
            return rooms
    except Exception as e:
        print(f"[WS_ROOMS] Error loading rooms from file: {e}")
    return {}


def _get_ws_room(code):
    """Get a room by code. Redis-backed with in-memory fallback."""
    if REDIS_AVAILABLE and redis_client:
        try:
            data = redis_client.get(f'ws_room:{code}')
            if data:
                room = json.loads(data)
                room['scores'] = {int(k): v for k, v in room.get('scores', {}).items()}
                room['judges'] = {int(k): v for k, v in room.get('judges', {}).items()}
                return room
            return None
        except Exception as e:
            print(f"[REDIS] Error getting room {code}: {e}")
    return _ws_rooms_memory.get(code)


def _set_ws_room(code, room):
    """Save a room. Redis-backed with in-memory fallback."""
    if REDIS_AVAILABLE and redis_client:
        try:
            saveable = dict(room)
            saveable['judges'] = {str(k): v for k, v in saveable.get('judges', {}).items()}
            saveable['scores'] = {str(k): v for k, v in saveable.get('scores', {}).items()}
            saveable.pop('video', None)
            redis_client.set(f'ws_room:{code}', json.dumps(saveable))
            return
        except Exception as e:
            print(f"[REDIS] Error saving room {code}: {e}")
    _ws_rooms_memory[code] = room
    _save_ws_rooms_to_file()


def _del_ws_room(code):
    """Delete a room."""
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.delete(f'ws_room:{code}')
            return
        except Exception as e:
            print(f"[REDIS] Error deleting room {code}: {e}")
    _ws_rooms_memory.pop(code, None)
    _save_ws_rooms_to_file()


def _get_all_ws_rooms():
    """Get all rooms as a dict."""
    if REDIS_AVAILABLE and redis_client:
        try:
            rooms = {}
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor, match='ws_room:*', count=100)
                if keys:
                    pipe = redis_client.pipeline()
                    for key in keys:
                        pipe.get(key)
                    values = pipe.execute()
                    for key, val in zip(keys, values):
                        if val:
                            code = key.replace('ws_room:', '', 1)
                            room = json.loads(val)
                            room['scores'] = {int(k): v for k, v in room.get('scores', {}).items()}
                            room['judges'] = {int(k): v for k, v in room.get('judges', {}).items()}
                            rooms[code] = room
                if cursor == 0:
                    break
            return rooms
        except Exception as e:
            print(f"[REDIS] Error getting all rooms: {e}")
    return dict(_ws_rooms_memory)


def _ws_room_exists(code):
    """Check if a room exists."""
    if REDIS_AVAILABLE and redis_client:
        try:
            return redis_client.exists(f'ws_room:{code}') > 0
        except Exception as e:
            print(f"[REDIS] Error checking room {code}: {e}")
    return code in _ws_rooms_memory


# --- Initialize rooms ---

# Load from file into memory (fallback data source)
_ws_rooms_memory = _load_ws_rooms_from_file()

# One-time migration from JSON file to Redis
if REDIS_AVAILABLE and redis_client:
    try:
        existing_keys = redis_client.keys('ws_room:*')
        if not existing_keys and _ws_rooms_memory:
            print(f"[REDIS] Migrating {len(_ws_rooms_memory)} rooms from JSON to Redis...")
            for code, room in _ws_rooms_memory.items():
                _set_ws_room(code, room)
            migrated_path = WS_ROOMS_FILE + '.migrated'
            if os.path.exists(WS_ROOMS_FILE):
                os.rename(WS_ROOMS_FILE, migrated_path)
            print("[REDIS] Migration complete")
    except Exception as e:
        print(f"[REDIS] Migration error: {e}")


def generate_room_code():
    """Generate a 6-character alphanumeric room code."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        code = ''.join(random.choices(chars, k=6))
        if not _ws_room_exists(code):
            return code
    return ''.join(random.choices(chars, k=8))


def _ensure_permanent_rooms():
    """Ensure all permanent rooms exist. Create missing ones, don't overwrite existing."""
    for code, definition in PERMANENT_ROOMS.items():
        room = _get_ws_room(code)
        if room is None:
            new_room = {
                'event_judge_name': definition['name'],
                'scoring_type': definition['scoring_type'],
                'panel_size': definition['panel_size'],
                'judges': {},
                'scores': {j: {} for j in range(1, definition['panel_size'] + 1)},
                'state': 'scoring',
                'permanent': True,
                'allowed_types': definition.get('allowed_types'),
                'created_at': datetime.now().isoformat(),
            }
            _set_ws_room(code, new_room)
        else:
            changed = False
            if not room.get('permanent'):
                room['permanent'] = True
                changed = True
            if 'allowed_types' not in room and definition.get('allowed_types'):
                room['allowed_types'] = definition['allowed_types']
                changed = True
            if changed:
                _set_ws_room(code, room)


_ensure_permanent_rooms()


def _reset_all_connected_flags():
    """On startup, mark all judges as disconnected (no sockets survive a restart)."""
    all_rooms = _get_all_ws_rooms()
    for code, room in all_rooms.items():
        changed = False
        for judge_num, judge in room.get('judges', {}).items():
            if judge.get('connected'):
                judge['connected'] = False
                changed = True
        if changed:
            _set_ws_room(code, room)


_reset_all_connected_flags()


# --- Helper ---

def _ws_scoring_completion(room):
    """Compute per-judge completion status."""
    required_fields = WS_SCORE_FIELDS.get(room['scoring_type'], {})
    panel_size = room.get('panel_size', 3)
    completion = {}
    for j in range(1, panel_size + 1):
        judge_scores = room.get('scores', {}).get(j, {})
        missing = []
        for field, (min_val, max_val) in required_fields.items():
            v = judge_scores.get(field)
            if v is None:
                missing.append(field)
            elif v < min_val or v > max_val:
                missing.append(field)
        completion[j] = {'complete': len(missing) == 0, 'missing': missing}
    return completion


def _resolve_video_url(url):
    """Resolve a video URL into playback info."""
    if not url:
        return None
    url = url.strip()
    # Direct video file URLs
    video_exts = ('.mp4', '.webm', '.ogg', '.mov')
    if any(url.lower().split('?')[0].endswith(ext) for ext in video_exts):
        return {'video_src': url, 'is_direct_url': True}
    # YouTube
    yt_match = re.match(r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)', url)
    if yt_match:
        return {'embed_url': f'https://www.youtube.com/embed/{yt_match.group(1)}', 'is_direct_url': False}
    # Vimeo
    vm_match = re.match(r'(?:https?://)?(?:www\.)?vimeo\.com/(\d+)', url)
    if vm_match:
        return {'embed_url': f'https://player.vimeo.com/video/{vm_match.group(1)}', 'is_direct_url': False}
    # Fallback: treat as direct URL
    return {'video_src': url, 'is_direct_url': True}


# --- HTTP routes for scoring ---

@app.route('/scoring/create', methods=['POST'])
@login_required
def create_ws_scoring_room():
    """Create a scoring room."""
    data = request.json
    scoring_type = data.get('scoring_type', 'ws-free')

    if scoring_type not in WS_SCORE_FIELDS:
        return jsonify({'error': 'Invalid scoring type'}), 400

    panel_size = PANEL_SIZES.get(scoring_type, 5)
    room_code = generate_room_code()
    room = {
        'event_judge_name': session.get('name', session.get('user', 'Unknown')),
        'scoring_type': scoring_type,
        'panel_size': panel_size,
        'judges': {},
        'scores': {j: {} for j in range(1, panel_size + 1)},
        'state': 'scoring',
        'created_at': datetime.now().isoformat(),
    }

    video_url = data.get('video_url')
    if video_url:
        video = _resolve_video_url(video_url)
        if video:
            room['video_url'] = video_url
            room['video'] = video

    _set_ws_room(room_code, room)
    return jsonify({'success': True, 'room_code': room_code})


@app.route('/scoring/join/<room_code>')
def ws_scoring_join_page(room_code):
    """Serve the judge scoring page (no login required)."""
    room = _get_ws_room(room_code)
    if not room:
        return "Room not found", 404

    video = room.get('video')
    if not video and room.get('video_url'):
        video = _resolve_video_url(room['video_url'])

    return render_template('judge_scoring.html',
                           room_code=room_code,
                           event_judge_name=room['event_judge_name'],
                           scoring_type=room['scoring_type'],
                           panel_size=room.get('panel_size', 5),
                           video=video,
                           is_permanent=room.get('permanent', False),
                           room_name=PERMANENT_ROOMS.get(room_code, {}).get('name', ''),
                           allowed_types=room.get('allowed_types'),
                           score_fields=WS_SCORE_FIELDS)


@app.route('/scoring/<room_code>/status')
def ws_scoring_room_status(room_code):
    """Get scoring room status."""
    room = _get_ws_room(room_code)
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    return jsonify({
        'state': room['state'],
        'judges': {str(k): {'name': v['name'], 'connected': v.get('connected', False)}
                   for k, v in room.get('judges', {}).items()},
        'scores': {str(k): v for k, v in room.get('scores', {}).items()},
        'scoring_type': room['scoring_type'],
    })


@app.route('/scoring/attach-video', methods=['POST'])
@login_required
def ws_scoring_attach_video():
    """Attach a video URL to an existing scoring room."""
    data = request.json
    room_code = data.get('room_code')
    video_url = data.get('video_url')

    room = _get_ws_room(room_code)
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    if video_url:
        video = _resolve_video_url(video_url)
        if video:
            room['video_url'] = video_url
            room['video'] = video
            _set_ws_room(room_code, room)
            if SOCKETIO_ENABLED and socketio:
                socketio.emit('ws_scoring_video_attached', video, room=room_code)

    return jsonify({'success': True})


@app.route('/scoring/permanent-rooms')
@login_required
def list_permanent_rooms():
    """List all permanent scoring rooms and their current state."""
    result = {}
    for code, definition in PERMANENT_ROOMS.items():
        room = _get_ws_room(code) or {}
        result[code] = {
            'name': definition['name'],
            'scoring_type': room.get('scoring_type', definition['scoring_type']),
            'panel_size': room.get('panel_size', definition['panel_size']),
            'state': room.get('state', 'scoring'),
            'judges': {str(k): {'name': v.get('name', ''), 'connected': v.get('connected', False)}
                       for k, v in room.get('judges', {}).items()},
            'allowed_types': definition.get('allowed_types'),
        }
    return jsonify(result)


# --- Socket.IO event handlers ---

if SOCKETIO_ENABLED:

    @socketio.on('ws_scoring_join')
    def on_ws_scoring_join(data):
        """Judge joins a scoring room."""
        room_code = data.get('room_code')
        judge_num = int(data.get('judge_num', 0))
        judge_name = data.get('judge_name', 'Anonymous')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        panel_size = room.get('panel_size', 3)
        if judge_num < 1 or judge_num > panel_size:
            emit('ws_scoring_error', {'message': 'Invalid judge position'})
            return

        if judge_num in room.get('judges', {}) and room['judges'][judge_num].get('connected'):
            existing_name = room['judges'][judge_num].get('name', '')
            existing_sid = room['judges'][judge_num].get('sid')
            if existing_sid != request.sid and existing_name != judge_name:
                emit('ws_scoring_error', {'message': f'J{judge_num} position already taken by {existing_name}'})
                return

        # Preserve confirmed state on reconnect
        was_confirmed = False
        if judge_num in room.get('judges', {}):
            prev = room['judges'][judge_num]
            if prev.get('name') == judge_name or prev.get('sid') == request.sid:
                was_confirmed = prev.get('confirmed', False)

        room.setdefault('judges', {})[judge_num] = {
            'name': judge_name,
            'connected': True,
            'sid': request.sid,
            'confirmed': was_confirmed,
        }
        join_room(room_code)
        _set_ws_room(room_code, room)

        video_info = room.get('video')
        if not video_info and room.get('video_url'):
            video_info = _resolve_video_url(room['video_url'])

        emit('ws_scoring_joined', {
            'judge_num': judge_num,
            'scoring_type': room['scoring_type'],
            'state': room['state'],
            'scores': room.get('scores', {}).get(judge_num, {}),
            'completion': _ws_scoring_completion(room),
            'video': video_info,
        })

        emit('ws_scoring_room_update', {
            'judges': {str(k): {'name': v['name'], 'connected': v.get('connected', False), 'confirmed': v.get('confirmed', False)}
                       for k, v in room['judges'].items()},
            'state': room['state'],
            'scores': {str(k): v for k, v in room.get('scores', {}).items()},
            'completion': _ws_scoring_completion(room),
        }, room=room_code)

    @socketio.on('ws_scoring_event_judge_join')
    def on_ws_scoring_event_judge_join(data):
        """Event judge connects to receive score updates."""
        room_code = data.get('room_code')

        if not _ws_room_exists(room_code):
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        room = _get_ws_room(room_code)
        join_room(room_code)

        emit('ws_scoring_room_update', {
            'judges': {str(k): {'name': v['name'], 'connected': v.get('connected', False), 'confirmed': v.get('confirmed', False)}
                       for k, v in room.get('judges', {}).items()},
            'state': room['state'],
            'scores': {str(k): v for k, v in room.get('scores', {}).items()},
            'scoring_type': room['scoring_type'],
            'completion': _ws_scoring_completion(room),
        })

    @socketio.on('ws_scoring_set_type')
    def on_ws_scoring_set_type(data):
        """Event judge switches scoring type."""
        room_code = data.get('room_code')
        new_type = data.get('scoring_type')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        allowed = room.get('allowed_types')
        if allowed:
            if new_type not in allowed:
                emit('ws_scoring_error', {'message': f'This room only supports: {", ".join(allowed)}'})
                return
        elif new_type not in WS_SCORE_FIELDS:
            emit('ws_scoring_error', {'message': 'Invalid scoring type'})
            return

        new_panel = PANEL_SIZES.get(new_type, 5)
        panel_size = max(room.get('panel_size', new_panel), new_panel)
        room['scoring_type'] = new_type
        room['panel_size'] = panel_size
        room['scores'] = {j: {} for j in range(1, panel_size + 1)}
        room['state'] = 'scoring'
        _set_ws_room(room_code, room)

        emit('ws_scoring_type_changed', {
            'scoring_type': new_type,
            'state': 'scoring',
            'scores': {str(k): v for k, v in room['scores'].items()},
            'completion': _ws_scoring_completion(room),
        }, room=room_code)

    @socketio.on('ws_scoring_submit')
    def on_ws_scoring_submit(data):
        """Judge submits/updates a score field."""
        room_code = data.get('room_code')
        judge_num = int(data.get('judge_num', 0))
        field = data.get('field')
        value = data.get('value')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        if room['state'] == 'complete':
            emit('ws_scoring_error', {'message': 'Scoring is locked'})
            return

        panel_size = room.get('panel_size', 3)
        if judge_num < 1 or judge_num > panel_size:
            emit('ws_scoring_error', {'message': 'Invalid judge position'})
            return

        valid_fields = WS_SCORE_FIELDS.get(room['scoring_type'], {})
        if field not in valid_fields:
            emit('ws_scoring_error', {'message': f'Invalid field: {field}'})
            return

        min_val, max_val = valid_fields[field]
        try:
            value = float(value) if value not in (None, '') else None
        except (ValueError, TypeError):
            value = None

        if value is not None and (value < min_val or value > max_val):
            emit('ws_scoring_error', {'message': f'{field} must be between {min_val} and {max_val}'})
            return

        if judge_num not in room.get('scores', {}):
            room['scores'][judge_num] = {}
        room['scores'][judge_num][field] = value
        _set_ws_room(room_code, room)

        completion = _ws_scoring_completion(room)

        emit('ws_scoring_score_update', {
            'judge_num': judge_num,
            'field': field,
            'value': value,
            'all_scores': {str(k): v for k, v in room['scores'].items()},
            'completion': completion,
        }, room=room_code)

    @socketio.on('ws_scoring_confirm')
    def on_ws_scoring_confirm(data):
        """Judge confirms all their scores at once."""
        room_code = data.get('room_code')
        judge_num = int(data.get('judge_num', 0))
        scores = data.get('scores', {})

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        if room['state'] != 'scoring':
            emit('ws_scoring_error', {'message': 'Cannot confirm - scoring not active'})
            return

        panel_size = room.get('panel_size', 3)
        if judge_num < 1 or judge_num > panel_size:
            emit('ws_scoring_error', {'message': 'Invalid judge position'})
            return

        if judge_num not in room.get('judges', {}):
            emit('ws_scoring_error', {'message': 'Judge not found in room'})
            return

        # Validate all required fields
        valid_fields = WS_SCORE_FIELDS.get(room['scoring_type'], {})
        validated_scores = {}
        for field, (min_val, max_val) in valid_fields.items():
            val = scores.get(field)
            if val is None or val == '':
                emit('ws_scoring_error', {'message': f'Missing field: {field}'})
                return
            try:
                val = float(val)
            except (ValueError, TypeError):
                emit('ws_scoring_error', {'message': f'Invalid value for {field}'})
                return
            if val < min_val or val > max_val:
                emit('ws_scoring_error', {'message': f'{field} must be between {min_val} and {max_val}'})
                return
            validated_scores[field] = val

        # Store scores and mark confirmed
        room['scores'][judge_num] = validated_scores
        room['judges'][judge_num]['confirmed'] = True
        _set_ws_room(room_code, room)

        # Check if all connected judges confirmed
        all_confirmed = all(
            room['judges'][j].get('confirmed', False)
            for j in range(1, panel_size + 1)
            if j in room['judges'] and room['judges'][j].get('connected', False)
        )

        emit('ws_scoring_score_confirmed', {
            'judge_num': judge_num,
            'judge_name': room['judges'][judge_num]['name'],
            'scores': validated_scores,
            'all_scores': {str(k): v for k, v in room['scores'].items()},
            'all_confirmed': all_confirmed,
            'confirmed_judges': {str(j): room['judges'][j].get('confirmed', False) for j in room['judges']},
        }, room=room_code)

    @socketio.on('ws_scoring_finalize')
    def on_ws_scoring_finalize(data):
        """Event judge finalizes scores - shows overlay then resets for next video."""
        room_code = data.get('room_code')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        panel_size = room.get('panel_size', 3)

        # Validate all connected judges confirmed
        for j in range(1, panel_size + 1):
            if j in room.get('judges', {}) and room['judges'][j].get('connected', False):
                if not room['judges'][j].get('confirmed', False):
                    emit('ws_scoring_error', {'message': f'J{j} has not confirmed their score'})
                    return

        # Snapshot current scores + judges for overlay
        final_scores = {str(k): dict(v) for k, v in room['scores'].items()}
        final_judges = {str(j): {'name': info.get('name', '?')} for j, info in room['judges'].items()}

        # Reset room for next video (preserve panel_size and judges)
        room['scores'] = {j: {} for j in range(1, panel_size + 1)}
        room['state'] = 'scoring'
        for j in room['judges']:
            room['judges'][j]['confirmed'] = False
        room.pop('video', None)
        room.pop('video_url', None)
        _set_ws_room(room_code, room)

        emit('ws_scoring_finalized', {
            'scores': final_scores,
            'judges': final_judges,
            'scoring_type': room['scoring_type'],
        }, room=room_code)

    @socketio.on('ws_scoring_lock')
    def on_ws_scoring_lock(data):
        """Event judge locks scores."""
        room_code = data.get('room_code')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        completion = _ws_scoring_completion(room)
        panel_size = room.get('panel_size', 3)
        errors = []
        for j in range(1, panel_size + 1):
            if not completion[j]['complete']:
                missing_str = ', '.join(completion[j]['missing'])
                errors.append(f'J{j} missing: {missing_str}')

        if errors:
            emit('ws_scoring_error', {'message': 'Cannot lock - ' + '; '.join(errors)})
            return

        room['state'] = 'complete'
        _set_ws_room(room_code, room)

        emit('ws_scoring_state_change', {
            'state': 'complete',
            'completion': completion,
        }, room=room_code)

    @socketio.on('ws_scoring_reset')
    def on_ws_scoring_reset(data):
        """Event judge resets all scores."""
        room_code = data.get('room_code')

        room = _get_ws_room(room_code)
        if not room:
            emit('ws_scoring_error', {'message': 'Room not found'})
            return

        panel_size = room.get('panel_size', 3)
        room['scores'] = {j: {} for j in range(1, panel_size + 1)}
        room['state'] = 'scoring'
        for j in room.get('judges', {}):
            room['judges'][j]['confirmed'] = False
        _set_ws_room(room_code, room)

        emit('ws_scoring_reset_all', {
            'state': 'scoring',
            'scores': {str(k): v for k, v in room['scores'].items()},
            'scoring_type': room['scoring_type'],
            'completion': _ws_scoring_completion(room),
        }, room=room_code)

    @socketio.on('ws_scoring_leave')
    def on_ws_scoring_leave(data):
        """Judge disconnects from scoring room."""
        room_code = data.get('room_code')
        judge_num = int(data.get('judge_num', 0))

        room = _get_ws_room(room_code)
        if not room:
            return

        if judge_num in room.get('judges', {}):
            room['judges'][judge_num]['connected'] = False
            _set_ws_room(room_code, room)

        leave_room(room_code)

        emit('ws_scoring_room_update', {
            'judges': {str(k): {'name': v['name'], 'connected': v.get('connected', False)}
                       for k, v in room.get('judges', {}).items()},
            'state': room['state'],
            'scores': {str(k): v for k, v in room.get('scores', {}).items()},
        }, room=room_code)

    @socketio.on('disconnect')
    def on_disconnect():
        """Handle unexpected disconnection - mark judge as disconnected."""
        all_rooms = _get_all_ws_rooms()
        for code, room in all_rooms.items():
            for judge_num, judge in room.get('judges', {}).items():
                if judge.get('sid') == request.sid and judge.get('connected'):
                    judge['connected'] = False
                    _set_ws_room(code, room)
                    if SOCKETIO_ENABLED and socketio:
                        socketio.emit('ws_scoring_room_update', {
                            'judges': {str(k): {'name': v['name'], 'connected': v.get('connected', False)}
                                       for k, v in room.get('judges', {}).items()},
                            'state': room['state'],
                            'scores': {str(k): v for k, v in room.get('scores', {}).items()},
                        }, room=code)
                    break

    # Video sync events
    @socketio.on('ws_scoring_video_play')
    def on_ws_scoring_video_play(data):
        room_code = data.get('room_code')
        if _ws_room_exists(room_code):
            emit('ws_scoring_video_play', {'time': data.get('time', 0)}, room=room_code, include_self=False)

    @socketio.on('ws_scoring_video_pause')
    def on_ws_scoring_video_pause(data):
        room_code = data.get('room_code')
        if _ws_room_exists(room_code):
            emit('ws_scoring_video_pause', {'time': data.get('time', 0)}, room=room_code, include_self=False)

    @socketio.on('ws_scoring_video_seek')
    def on_ws_scoring_video_seek(data):
        room_code = data.get('room_code')
        if _ws_room_exists(room_code):
            emit('ws_scoring_video_seek', {'time': data.get('time', 0)}, room=room_code, include_self=False)


# ==================== APP ENTRY POINT ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print("\n=== USPA Judge Test ===")
    print(f"Open http://localhost:{port} in your browser")
    print("\nDefault logins:")
    print("  Admin: admin / admin123")
    print("  Student: student / student123\n")
    if SOCKETIO_ENABLED:
        socketio.run(app, debug=debug, host='0.0.0.0', port=port)
    else:
        app.run(debug=debug, host='0.0.0.0', port=port)
