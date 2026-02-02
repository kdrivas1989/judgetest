#!/usr/bin/env python3
"""USPA Judge Test - Web-based testing application for USPA judges."""

import os
import json
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'uspa-judge-test-secret-key-change-in-production')

# User database (in production, use a real database)
USERS = {
    'proctor': {'password': 'proctor123', 'role': 'proctor', 'name': 'Proctor'},
    'student': {'password': 'student123', 'role': 'student', 'name': 'Test Student'}
}

# Store test results
test_results = {}

# 25 Multiple Choice Questions from USPA SCM Chapters 1, 2, and 3
# Each question has a correct_section field for the fill-in reference answer
QUESTIONS = [
    # Chapter 1 Questions (1-12)
    {
        "id": 1,
        "question": "What is the minimum number of freefall skydives required to compete at a USPA National Skydiving Championships?",
        "options": ["50 skydives", "75 skydives", "100 skydives", "200 skydives"],
        "correct": 2,
        "correct_section": "1.5.2.1",
        "reference": "SCM Chapter 1, Section 5.2.1"
    },
    {
        "id": 2,
        "question": "What is the minimum age requirement to compete at USPA Nationals?",
        "options": ["16 years", "17 years", "18 years", "21 years"],
        "correct": 2,
        "correct_section": "1.5.2.1",
        "reference": "SCM Chapter 1, Section 5.2.1"
    },
    {
        "id": 3,
        "question": "What is the maximum wind limit for all events unless otherwise specified?",
        "options": ["7 m/s", "9 m/s", "11 m/s", "13 m/s"],
        "correct": 2,
        "correct_section": "1.8.3.2",
        "reference": "SCM Chapter 1, Section 8.3.2"
    },
    {
        "id": 4,
        "question": "How long do competitors have to file a protest after knowledge of the grounds for protest?",
        "options": ["30 minutes", "1 hour", "2 hours", "24 hours"],
        "correct": 2,
        "correct_section": "1.8.4.2",
        "reference": "SCM Chapter 1, Section 8.4.2"
    },
    {
        "id": 5,
        "question": "What fee must accompany a protest?",
        "options": ["$25", "$50", "$75", "$100"],
        "correct": 1,
        "correct_section": "1.8.4.4",
        "reference": "SCM Chapter 1, Section 8.4.4"
    },
    {
        "id": 6,
        "question": "What is the minimum license required for Formation Skydiving Open class?",
        "options": ["A License", "B License", "C License", "D License"],
        "correct": 2,
        "correct_section": "Table 1",
        "reference": "SCM Chapter 1, Table 1"
    },
    {
        "id": 7,
        "question": "The Meet Director may NOT be a competitor in any event.",
        "options": ["True", "False", "Only in team events", "Only with Jury approval"],
        "correct": 0,
        "correct_section": "1.4.1.4",
        "reference": "SCM Chapter 1, Section 4.1.4"
    },
    {
        "id": 8,
        "question": "How many aircraft passes over the target are permitted per competitor or team for any jump?",
        "options": ["One", "Two", "Three", "Unlimited"],
        "correct": 1,
        "correct_section": "1.8.2.3.1",
        "reference": "SCM Chapter 1, Section 8.2.3.1"
    },
    {
        "id": 9,
        "question": "What is the minimum advance call time before boarding the aircraft?",
        "options": ["5 minutes only", "10 and 5 minutes", "15 and 5 minutes", "20 and 10 minutes"],
        "correct": 2,
        "correct_section": "1.7.5.1",
        "reference": "SCM Chapter 1, Section 7.5.1"
    },
    {
        "id": 10,
        "question": "For Open class Canopy Piloting, how many high-performance landings are required in the last 12 months?",
        "options": ["100 landings", "125 landings", "150 landings", "200 landings"],
        "correct": 2,
        "correct_section": "1.5.2.2",
        "reference": "SCM Chapter 1, Section 5.2.2"
    },
    {
        "id": 11,
        "question": "The panel of judges for a discipline must comprise an event judge plus at least how many other judges?",
        "options": ["Two", "Three", "Four", "Five"],
        "correct": 1,
        "correct_section": "1.4.2.4",
        "reference": "SCM Chapter 1, Section 4.2.4"
    },
    {
        "id": 12,
        "question": "What is the penalty for failure to meet video requirements at a USPA Nationals?",
        "options": ["Zero score", "10% score penalty", "20% score penalty", "Rejump required"],
        "correct": 2,
        "correct_section": "1.7.3.1",
        "reference": "SCM Chapter 1, Section 7.3.1"
    },

    # Chapter 2 Questions (13-20)
    {
        "id": 13,
        "question": "What is the minimum score required on the written exam for a Regional Judge rating?",
        "options": ["70%", "75%", "80%", "85%"],
        "correct": 1,
        "correct_section": "2.6.5.2",
        "reference": "SCM Chapter 2, Section 6.5.2"
    },
    {
        "id": 14,
        "question": "What is the minimum score required on the practical exam for a National Judge rating?",
        "options": ["75%", "80%", "85%", "90%"],
        "correct": 2,
        "correct_section": "2.6.5.3",
        "reference": "SCM Chapter 2, Section 6.5.3"
    },
    {
        "id": 15,
        "question": "How long is a USPA member required to have been a member before earning a National Judge rating?",
        "options": ["6 months", "1 year", "2 years", "3 years"],
        "correct": 1,
        "correct_section": "2.3.2.1",
        "reference": "SCM Chapter 2, Section 3.2.1"
    },
    {
        "id": 16,
        "question": "What is the initial judge rating fee that includes a logbook?",
        "options": ["$25", "$35", "$45", "$50"],
        "correct": 1,
        "correct_section": "2.3.5.1.2",
        "reference": "SCM Chapter 2, Section 3.5.1.2"
    },
    {
        "id": 17,
        "question": "A National Judge rating automatically expires at the end of which calendar year?",
        "options": ["Third year", "Fourth year", "Fifth year", "Seventh year"],
        "correct": 2,
        "correct_section": "2.5.1.2.1",
        "reference": "SCM Chapter 2, Section 5.1.2.1"
    },
    {
        "id": 18,
        "question": "By what date must judges contact the Director of Competition to be on the active judges list?",
        "options": ["October 1", "November 1", "December 1", "January 1"],
        "correct": 2,
        "correct_section": "2.5.2.1",
        "reference": "SCM Chapter 2, Section 5.2.1"
    },
    {
        "id": 19,
        "question": "To apply for Judge Examiner appointment, how many consecutive years must a judge be on the active list?",
        "options": ["Three years", "Four years", "Five years", "Seven years"],
        "correct": 2,
        "correct_section": "2.6.3.1",
        "reference": "SCM Chapter 2, Section 6.3.1"
    },
    {
        "id": 20,
        "question": "A Regional Judge's rating is valid for how long?",
        "options": ["1 year", "3 years", "5 years", "Permanent with USPA membership"],
        "correct": 3,
        "correct_section": "2.5.1.1.1",
        "reference": "SCM Chapter 2, Section 5.1.1.1"
    },

    # Chapter 3 and Mixed Questions (21-25)
    {
        "id": 21,
        "question": "Who has authority to impose regulations due to unforeseeable exigencies during competition?",
        "options": ["Chief Judge only", "Meet Director only", "Meet Management", "The Jury"],
        "correct": 2,
        "correct_section": "1.6.3.1",
        "reference": "SCM Chapter 1, Section 6.3.1"
    },
    {
        "id": 22,
        "question": "What is the minimum number of judges required for Accuracy Landing?",
        "options": ["Three judges", "Four judges", "Five judges", "Six judges"],
        "correct": 2,
        "correct_section": "1.4.2.6",
        "reference": "SCM Chapter 1, Section 4.2.6"
    },
    {
        "id": 23,
        "question": "How many years can the same person serve as Chief Judge in the same discipline consecutively?",
        "options": ["One year", "Two years", "Three years", "No limit"],
        "correct": 1,
        "correct_section": "2.7.3.1.1",
        "reference": "SCM Chapter 2, Section 7.3.1.1"
    },
    {
        "id": 24,
        "question": "What minimum score on absolute assessments is required for FS and CF judge training?",
        "options": ["70%", "75%", "80%", "85%"],
        "correct": 2,
        "correct_section": "2.6.6.1",
        "reference": "SCM Chapter 2, Section 6.6.1"
    },
    {
        "id": 25,
        "question": "What organization delegated USPA authority over skydiving competition in the United States?",
        "options": ["FAA", "FAI", "NAA", "ISC"],
        "correct": 2,
        "correct_section": "1.1.3.1",
        "reference": "SCM Chapter 1, Section 1.3.1"
    }
]


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
                         name=session.get('name'))


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


@app.route('/test')
@login_required
def take_test():
    if session.get('role') != 'student':
        return redirect(url_for('index'))
    return render_template('test.html', questions=QUESTIONS, total=len(QUESTIONS))


@app.route('/submit-test', methods=['POST'])
@login_required
def submit_test():
    if session.get('role') != 'student':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    answers = data.get('answers', {})
    sections = data.get('sections', {})

    # Grade the test
    # Scoring: 4 points if MC correct, 1 point if MC wrong but reference correct
    # Total possible: 25 questions × 4 points = 100 points
    total_points = 0
    results = []

    for q in QUESTIONS:
        q_id = str(q['id'])
        user_answer = answers.get(q_id)
        user_section = sections.get(q_id, '').strip().lower()
        correct_section = q['correct_section'].lower()

        is_correct = user_answer == q['correct']
        is_section_correct = user_section == correct_section

        # Calculate points for this question
        if is_correct:
            question_points = 4  # Full points for correct answer
        elif is_section_correct:
            question_points = 1  # Partial credit for correct reference
        else:
            question_points = 0

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
            'options': q['options'],
            'reference': q['reference']
        })

    total_possible = len(QUESTIONS) * 4  # 100 points
    score = round((total_points / total_possible) * 100, 1)
    passed = score >= 75  # 75% passing score

    # Store result
    result_id = str(uuid.uuid4())[:8]
    test_results[result_id] = {
        'student': session.get('name'),
        'username': session.get('user'),
        'score': score,
        'total_points': total_points,
        'total_possible': total_possible,
        'total_questions': len(QUESTIONS),
        'passed': passed,
        'timestamp': datetime.now().isoformat(),
        'results': results
    }

    return jsonify({
        'result_id': result_id,
        'score': score,
        'total_points': total_points,
        'total_possible': total_possible,
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
    return render_template('proctor.html', results=test_results)


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


@app.route('/answer-key')
@proctor_required
def answer_key():
    return render_template('answer_key.html', questions=QUESTIONS)


@app.route('/api/questions')
@login_required
def get_questions():
    # Return questions without correct answers for the test
    safe_questions = []
    for q in QUESTIONS:
        safe_questions.append({
            'id': q['id'],
            'question': q['question'],
            'options': q['options'],
            'ref_question': q['ref_question']
        })
    return jsonify(safe_questions)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print("\n=== USPA Judge Test ===")
    print(f"Open http://localhost:{port} in your browser")
    print("\nDefault logins:")
    print("  Proctor: proctor / proctor123")
    print("  Student: student / student123\n")
    app.run(debug=debug, host='0.0.0.0', port=port)
