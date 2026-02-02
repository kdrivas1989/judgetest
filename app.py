#!/usr/bin/env python3
"""USPA Judge Test - Web-based testing application for USPA judges.
   Using Supabase REST API for persistent data storage.
"""

import os
import uuid
import json
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g
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

# Database support - Supabase REST API for production, SQLite for local dev
import sqlite3  # Always available as fallback
try:
    from supabase import create_client, Client
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    if SUPABASE_URL and SUPABASE_KEY:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        USE_SUPABASE = True
    else:
        USE_SUPABASE = False
        supabase = None
except ImportError:
    USE_SUPABASE = False
    supabase = None

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'uspa-judge-test-secret-key-change-in-production')

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


def get_sqlite_db():
    """Get SQLite database connection for local development."""
    if 'db' not in g:
        g.db = sqlite3.connect('judgetest.db')
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
    if USE_SUPABASE:
        # For Supabase, check if admin exists and create if not
        try:
            result = supabase.table('users').select('username').eq('username', 'admin').execute()
            if not result.data:
                supabase.table('users').insert({
                    'username': 'admin',
                    'password': 'admin123',
                    'role': 'admin',
                    'name': 'Administrator',
                    'categories': '[]',
                    'assigned_tests': '[]'
                }).execute()
        except Exception as e:
            print(f"Supabase init error (tables may need to be created manually): {e}")
    else:
        conn = sqlite3.connect('judgetest.db')
        cursor = conn.cursor()

        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                categories TEXT DEFAULT '[]',
                assigned_tests TEXT DEFAULT '[]'
            )
        ''')

        # Add assigned_tests column if it doesn't exist (migration for SQLite)
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN assigned_tests TEXT DEFAULT "[]"')
        except:
            pass  # Column already exists

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

        # Add default admin if not exists
        cursor.execute('SELECT username FROM users WHERE username = ?', ('admin',))
        if not cursor.fetchone():
            cursor.execute(
                'INSERT INTO users (username, password, role, name, categories, assigned_tests) VALUES (?, ?, ?, ?, ?, ?)',
                ('admin', 'admin123', 'admin', 'Administrator', '[]', '[]')
            )

        conn.commit()
        conn.close()


def get_user(username):
    """Get user from database."""
    if USE_SUPABASE:
        result = supabase.table('users').select('*').eq('username', username).execute()
        if result.data:
            row = result.data[0]
            return {
                'password': row['password'],
                'role': row['role'],
                'name': row['name'],
                'categories': json.loads(row['categories']) if row['categories'] else [],
                'assigned_tests': json.loads(row['assigned_tests']) if row.get('assigned_tests') else [],
                'proctor_level': row.get('proctor_level', 'regional'),
                'expiration_date': row.get('expiration_date', '')
            }
        return None
    else:
        db = get_sqlite_db()
        cursor = db.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if row:
            assigned_tests = row['assigned_tests'] if 'assigned_tests' in row.keys() else '[]'
            proctor_level = row['proctor_level'] if 'proctor_level' in row.keys() else 'regional'
            expiration_date = row['expiration_date'] if 'expiration_date' in row.keys() else ''
            return {
                'password': row['password'],
                'role': row['role'],
                'name': row['name'],
                'categories': json.loads(row['categories']),
                'assigned_tests': json.loads(assigned_tests) if assigned_tests else [],
                'proctor_level': proctor_level,
                'expiration_date': expiration_date or ''
            }
        return None


def get_all_users():
    """Get all users from database."""
    if USE_SUPABASE:
        result = supabase.table('users').select('*').execute()
        users = {}
        for row in result.data:
            users[row['username']] = {
                'password': row['password'],
                'role': row['role'],
                'name': row['name'],
                'categories': json.loads(row['categories']) if row['categories'] else [],
                'assigned_tests': json.loads(row['assigned_tests']) if row.get('assigned_tests') else [],
                'proctor_level': row.get('proctor_level', 'regional'),
                'expiration_date': row.get('expiration_date', '')
            }
        return users
    else:
        db = get_sqlite_db()
        cursor = db.execute('SELECT * FROM users')
        users = {}
        for row in cursor.fetchall():
            assigned_tests = row['assigned_tests'] if 'assigned_tests' in row.keys() else '[]'
            proctor_level = row['proctor_level'] if 'proctor_level' in row.keys() else 'regional'
            expiration_date = row['expiration_date'] if 'expiration_date' in row.keys() else ''
            users[row['username']] = {
                'password': row['password'],
                'role': row['role'],
                'name': row['name'],
                'categories': json.loads(row['categories']),
                'assigned_tests': json.loads(assigned_tests) if assigned_tests else [],
                'proctor_level': proctor_level,
                'expiration_date': expiration_date or ''
            }
        return users


def save_user(username, user_data):
    """Save user to database."""
    categories = json.dumps(user_data.get('categories', []))
    assigned_tests = json.dumps(user_data.get('assigned_tests', []))
    proctor_level = user_data.get('proctor_level', 'regional')
    expiration_date = user_data.get('expiration_date', '') or None
    if USE_SUPABASE:
        # Try update first, then insert if not exists
        existing = supabase.table('users').select('username').eq('username', username).execute()
        if existing.data:
            supabase.table('users').update({
                'password': user_data['password'],
                'role': user_data['role'],
                'name': user_data['name'],
                'categories': categories,
                'assigned_tests': assigned_tests,
                'proctor_level': proctor_level,
                'expiration_date': expiration_date
            }).eq('username', username).execute()
        else:
            supabase.table('users').insert({
                'username': username,
                'password': user_data['password'],
                'role': user_data['role'],
                'name': user_data['name'],
                'categories': categories,
                'assigned_tests': assigned_tests,
                'proctor_level': proctor_level,
                'expiration_date': expiration_date
            }).execute()
    else:
        db = get_sqlite_db()
        db.execute('''
            INSERT OR REPLACE INTO users (username, password, role, name, categories, assigned_tests, proctor_level)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (username, user_data['password'], user_data['role'], user_data['name'], categories, assigned_tests, proctor_level))
        db.commit()


def delete_user(username):
    """Delete user from database."""
    if USE_SUPABASE:
        supabase.table('users').delete().eq('username', username).execute()
    else:
        db = get_sqlite_db()
        db.execute('DELETE FROM users WHERE username = ?', (username,))
        db.commit()


def get_test_result(result_id):
    """Get test result from database."""
    if USE_SUPABASE:
        result = supabase.table('test_results').select('data').eq('result_id', result_id).execute()
        if result.data:
            return json.loads(result.data[0]['data'])
        return None
    else:
        db = get_sqlite_db()
        cursor = db.execute('SELECT data FROM test_results WHERE result_id = ?', (result_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row['data'])
        return None


def get_all_test_results():
    """Get all test results from database."""
    if USE_SUPABASE:
        result = supabase.table('test_results').select('result_id, data').execute()
        results = {}
        for row in result.data:
            results[row['result_id']] = json.loads(row['data'])
        return results
    else:
        db = get_sqlite_db()
        cursor = db.execute('SELECT result_id, data FROM test_results')
        results = {}
        for row in cursor.fetchall():
            results[row['result_id']] = json.loads(row['data'])
        return results


def save_test_result(result_id, result_data):
    """Save test result to database."""
    if USE_SUPABASE:
        # Try update first, then insert if not exists
        existing = supabase.table('test_results').select('result_id').eq('result_id', result_id).execute()
        if existing.data:
            supabase.table('test_results').update({
                'data': json.dumps(result_data)
            }).eq('result_id', result_id).execute()
        else:
            supabase.table('test_results').insert({
                'result_id': result_id,
                'data': json.dumps(result_data)
            }).execute()
    else:
        db = get_sqlite_db()
        db.execute(
            'INSERT OR REPLACE INTO test_results (result_id, data) VALUES (?, ?)',
            (result_id, json.dumps(result_data))
        )
        db.commit()


def get_custom_questions(test_id):
    """Get custom questions for a test from database."""
    if USE_SUPABASE:
        result = supabase.table('custom_questions').select('data').eq('test_id', test_id).execute()
        if result.data:
            return json.loads(result.data[0]['data'])
        return None
    else:
        db = get_sqlite_db()
        cursor = db.execute('SELECT data FROM custom_questions WHERE test_id = ?', (test_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row['data'])
        return None


def save_custom_questions(test_id, questions_data):
    """Save custom questions for a test to database."""
    if USE_SUPABASE:
        existing = supabase.table('custom_questions').select('test_id').eq('test_id', test_id).execute()
        if existing.data:
            supabase.table('custom_questions').update({
                'data': json.dumps(questions_data)
            }).eq('test_id', test_id).execute()
        else:
            supabase.table('custom_questions').insert({
                'test_id': test_id,
                'data': json.dumps(questions_data)
            }).execute()
    else:
        db = get_sqlite_db()
        db.execute(
            'INSERT OR REPLACE INTO custom_questions (test_id, data) VALUES (?, ?)',
            (test_id, json.dumps(questions_data))
        )
        db.commit()


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
    if USE_SUPABASE:
        try:
            result = supabase.table('tests').select('*').execute()
            if result.data:
                tests = {}
                for row in result.data:
                    tests[row['test_id']] = {
                        'name': row['name'],
                        'chapter': row['chapter'],
                        'passing_score': row['passing_score'],
                        'questions': json.loads(row['questions']) if row['questions'] else []
                    }
                return tests
        except Exception as e:
            print(f"Error loading tests from database: {e}")
    else:
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
    if USE_SUPABASE:
        existing = supabase.table('tests').select('test_id').eq('test_id', test_id).execute()
        if existing.data:
            supabase.table('tests').update({
                'name': test_data['name'],
                'chapter': test_data['chapter'],
                'passing_score': test_data['passing_score'],
                'questions': json.dumps(test_data['questions'])
            }).eq('test_id', test_id).execute()
        else:
            supabase.table('tests').insert({
                'test_id': test_id,
                'name': test_data['name'],
                'chapter': test_data['chapter'],
                'passing_score': test_data['passing_score'],
                'questions': json.dumps(test_data['questions'])
            }).execute()
    else:
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
        if session.get('role') not in ['proctor', 'admin']:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def get_proctor_tests(username, include_general=True):
    """Get tests available for a proctor based on assigned categories and per-category levels."""
    user = get_user(username)
    all_tests = get_all_tests()
    if not user:
        return {}
    if user['role'] == 'admin':
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

    role = session.get('role')
    if role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif role == 'proctor':
        return redirect(url_for('proctor_dashboard'))

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
    if session.get('role') != 'student':
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
    if session.get('role') != 'student':
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

    role = session.get('role')

    # Students can only view their own results
    if role == 'student' and result['username'] != session.get('user'):
        return "Unauthorized", 403

    # Proctors can only view results for their assigned categories
    if role == 'proctor':
        available_tests = get_proctor_tests(session.get('user'))
        if result['test_id'] not in available_tests:
            return "Unauthorized", 403

    # Check if examiner can approve references (non-passing tests only)
    can_approve = role in ['proctor', 'admin'] and not result.get('passed', True)

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
    role = session.get('role')
    if role == 'proctor':
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
    students = {u: data for u, data in all_users.items() if data['role'] == 'student'}

    # Add test status to each student
    all_results = get_all_test_results()
    for student_username, student_data in students.items():
        student_results = {rid: r for rid, r in all_results.items()
                         if r.get('student_username') == student_username or r.get('student') == student_data['name']}
        assigned = student_data.get('assigned_tests', [])
        completed = []
        for rid, result in student_results.items():
            test_id = result.get('test_id')
            if test_id:
                completed.append({
                    'test_id': test_id,
                    'passed': result.get('passed', False),
                    'score': result.get('score', 0)
                })
        student_data['completed_tests'] = completed
        student_data['tests_completed'] = len(completed)
        student_data['tests_assigned'] = len(assigned)

    return render_template('proctor.html',
                         results=available_results,
                         tests=available_tests,
                         students=students,
                         categories=category_names,
                         is_admin=(session.get('role') == 'admin'))


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
    if session.get('role') not in ['proctor', 'admin']:
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
    # Get all proctors and students
    all_users = get_all_users()
    proctors = {u: data for u, data in all_users.items() if data['role'] == 'proctor'}
    students = {u: data for u, data in all_users.items() if data['role'] == 'student'}
    all_results = get_all_test_results()

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

    return render_template('admin.html',
                         proctors=proctors,
                         examiners=examiners,
                         trainers=trainers,
                         students=students,
                         categories=CATEGORIES,
                         results=all_results,
                         tests=get_all_tests())


@app.route('/admin/add-proctor', methods=['POST'])
@admin_required
def add_proctor():
    data = request.json
    username = data.get('username', '').lower()
    name = data.get('name', '')
    categories = data.get('categories', {})  # Format: {cat_id: {"level": "...", "expiration": "..."}}

    if not username or not name:
        return jsonify({'error': 'Username and name are required'}), 400

    if get_user(username):
        return jsonify({'error': 'Username already exists'}), 400

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
    if not user or user['role'] != 'proctor':
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
    if not user or user['role'] != 'proctor':
        return jsonify({'error': 'Proctor not found'}), 404

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


@app.route('/admin/delete-student/<username>', methods=['POST'])
@admin_required
def admin_delete_student(username):
    user = get_user(username)
    if not user or user['role'] != 'student':
        return jsonify({'error': 'Student not found'}), 404

    delete_user(username)
    return jsonify({'success': True, 'message': 'Student deleted'})


@app.route('/admin/get-proctor/<username>')
@admin_required
def get_proctor_route(username):
    user = get_user(username)
    if not user or user['role'] != 'proctor':
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
        'source': 'database' if USE_SUPABASE else 'local'
    })


@app.route('/admin/migrate-categories', methods=['POST'])
@admin_required
def migrate_categories():
    """One-time migration to convert old category format to new format with per-category expiration."""
    all_users = get_all_users()
    migrated = 0

    for username, user_data in all_users.items():
        if user_data['role'] != 'proctor':
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print("\n=== USPA Judge Test ===")
    print(f"Open http://localhost:{port} in your browser")
    print("\nDefault logins:")
    print("  Admin: admin / admin123")
    print("  Student: student / student123\n")
    app.run(debug=debug, host='0.0.0.0', port=port)
