#!/usr/bin/env python3
"""USPA Judge Test - Web-based testing application for USPA judges."""

import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
from questions import TESTS

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'uspa-judge-test-secret-key-change-in-production')

# User database (in production, use a real database)
USERS = {
    'proctor': {'password': 'proctor123', 'role': 'proctor', 'name': 'Proctor'},
    'student': {'password': 'student123', 'role': 'student', 'name': 'Test Student'}
}

# Store test results
test_results = {}


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
        if session.get('role') != 'proctor':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html',
                         user=session.get('user'),
                         role=session.get('role'),
                         name=session.get('name'),
                         tests=TESTS)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').lower()
        password = request.form.get('password', '')

        if username in USERS and USERS[username]['password'] == password:
            session['user'] = username
            session['role'] = USERS[username]['role']
            session['name'] = USERS[username]['name']
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

    if test_id not in TESTS:
        return "Test not found", 404

    test = TESTS[test_id]
    return render_template('test.html',
                         questions=test['questions'],
                         total=len(test['questions']),
                         test_id=test_id,
                         test_name=test['name'],
                         passing_score=test['passing_score'])


@app.route('/submit-test/<test_id>', methods=['POST'])
@login_required
def submit_test(test_id):
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 403

    if test_id not in TESTS:
        return jsonify({'error': 'Test not found'}), 404

    test = TESTS[test_id]
    questions = test['questions']
    passing_score = test['passing_score']

    data = request.json
    answers = data.get('answers', {})
    sections = data.get('sections', {})

    # Grade the test
    # Scoring: 4 points if MC correct, 1 point if MC wrong but reference correct
    # Total possible: 25 questions × 4 points = 100 points
    total_points = 0
    results = []

    for q in questions:
        q_id = str(q['id'])
        user_answer = answers.get(q_id)
        user_section = sections.get(q_id, '').strip().lower()
        correct_section = q['correct_section'].lower()

        is_correct = user_answer == q['correct']
        is_section_correct = user_section == correct_section

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

    # Store result
    result_id = str(uuid.uuid4())[:8]
    test_results[result_id] = {
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
    }

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
    if result_id not in test_results:
        return "Results not found", 404

    result = test_results[result_id]

    # Students can only view their own results
    if session.get('role') == 'student' and result['username'] != session.get('user'):
        return "Unauthorized", 403

    return render_template('results.html', result=result, result_id=result_id)


@app.route('/proctor')
@proctor_required
def proctor_dashboard():
    return render_template('proctor.html', results=test_results, tests=TESTS)


@app.route('/answer-key/<test_id>')
@proctor_required
def answer_key(test_id):
    if test_id not in TESTS:
        return "Test not found", 404
    test = TESTS[test_id]
    return render_template('answer_key.html', questions=test['questions'], test_name=test['name'], test_id=test_id, tests=TESTS)


@app.route('/proctor/add-student', methods=['POST'])
@proctor_required
def add_student():
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password', '')
    name = data.get('name', '')

    if not username or not password or not name:
        return jsonify({'error': 'All fields required'}), 400

    if username in USERS:
        return jsonify({'error': 'Username already exists'}), 400

    USERS[username] = {
        'password': password,
        'role': 'student',
        'name': name
    }

    return jsonify({'success': True, 'message': f'Student {name} added'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print("\n=== USPA Judge Test ===")
    print(f"Open http://localhost:{port} in your browser")
    print("\nDefault logins:")
    print("  Proctor: proctor / proctor123")
    print("  Student: student / student123\n")
    app.run(debug=debug, host='0.0.0.0', port=port)
