# routes/quiz_ranking.py
import random
from flask import (
    render_template, request, redirect, url_for,
    session, flash
)
from app import db
from models import User
from routes.quiz_base import quiz_bp, ALL_TRACKS, get_buddy_personality_lines

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)

def require_login(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first!", "warning")
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return wrapper

@quiz_bp.route('/random_rank', methods=['GET'])
@require_login
def random_rank():
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    max_n = min(len(ALL_TRACKS), 4)  # Limit to a maximum of 4 tracks
    if max_n < 2:
        flash("Not enough tracks to rank!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    n = random.choice([2, 3, 4][:max_n])  # Ensure the number of tracks is 2, 3, or 4
    sample_tracks = random.sample(ALL_TRACKS, n)
    session['ranking_tracks'] = [t['id'] for t in sample_tracks]

    return render_template("random_rank_drag.html", tracks=sample_tracks)

@quiz_bp.route('/submit_random_rank', methods=['POST'])
@require_login
def submit_random_rank():
    user = current_user()
    ranking_mode = session.get('ranking_mode', 'timeline')
    track_ids = session.get('ranking_tracks', [])
    if not track_ids:
        flash("No tracks to rank! Please start again.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    drag_order = request.form.get("drag_order", "").strip()
    final_ids = []
    if drag_order:
        final_ids = [x for x in drag_order.split(",") if x]
    else:
        submitted_order = []
        for tid in track_ids:
            rank_str = request.form.get(f"rank_{tid}")
            if rank_str and rank_str.isdigit():
                submitted_order.append((tid, int(rank_str)))
        submitted_order.sort(key=lambda x: x[1])
        final_ids = [x[0] for x in submitted_order]

    final_ids = [x for x in final_ids if x in track_ids]
    if not final_ids:
        final_ids = track_ids

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    valid_tracks = [get_track(tid) for tid in track_ids if get_track(tid)]

    if ranking_mode == 'timeline':
        correct_ordered = sorted(valid_tracks, key=lambda t: t['year'])
    else:
        correct_ordered = sorted(valid_tracks, key=lambda t: t['popularity'])

    correct_ids_in_order = [t['id'] for t in correct_ordered]

    def count_correct_pairs(order_list):
        correct_count = 0
        total_pairs = 0
        for i in range(len(order_list)):
            for j in range(i+1, len(order_list)):
                total_pairs += 1
                tid_i = order_list[i]
                tid_j = order_list[j]
                idx_i = correct_ids_in_order.index(tid_i)
                idx_j = correct_ids_in_order.index(tid_j)
                if idx_i < idx_j:
                    correct_count += 1
        if total_pairs == 0:
            return 1.0
        return correct_count / total_pairs

    correctness_fraction = count_correct_pairs(final_ids)
    difficulty_str = request.form.get("difficulty", "3")
    try:
        difficulty_rating = int(difficulty_str)
    except:
        difficulty_rating = 3
    difficulty_rating = max(1, min(difficulty_rating, 5))
    rating_fraction = (difficulty_rating - 1) / 4
    outcome = 0.7 * correctness_fraction + 0.3 * rating_fraction

    user.update_elo('random', 'rank', outcome)
    db.session.commit()

    flash(f"You ranked {len(track_ids)} track(s). Correctness={correctness_fraction:.2f}, difficulty={difficulty_rating}, outcome={outcome:.2f}.", "info")
    session.pop('ranking_tracks', None)

    lines = get_buddy_personality_lines()
    if correctness_fraction > 0.8:
        session["buddy_message"] = random.choice(lines['correct'])
    else:
        session["buddy_message"] = random.choice(lines['wrong'])

    return redirect(url_for('quiz_bp.dashboard'))

@quiz_bp.route('/personalized_rank', methods=['GET'])
@require_login
def personalized_rank():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    elo = user.personalized_rank_elo
    if elo < 1100:
        n = 3
    elif elo > 1400:
        n = 5
    else:
        n = 4

    if len(ALL_TRACKS) < n:
        n = len(ALL_TRACKS)

    missed_list = user.get_missed_songs()
    missed_candidates = [t for t in ALL_TRACKS if t["id"] in missed_list]
    random.shuffle(missed_candidates)

    chosen = []
    while len(chosen) < n and missed_candidates:
        chosen.append(missed_candidates.pop())

    remaining_needed = n - len(chosen)
    if remaining_needed > 0:
        used_ids = [c['id'] for c in chosen]
        available = [t for t in ALL_TRACKS if t['id'] not in used_ids]
        chosen.extend(random.sample(available, remaining_needed))

    session['personalized_ranking_tracks'] = [t['id'] for t in chosen]
    return render_template("personalized_rank_drag.html", tracks=chosen)

@quiz_bp.route('/submit_personalized_rank', methods=['POST'])
@require_login
def submit_personalized_rank():
    user = current_user()
    ranking_mode = session.get('ranking_mode', 'timeline')
    track_ids = session.get('personalized_ranking_tracks', [])
    if not track_ids:
        flash("No tracks to rank! Please start again.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    drag_order = request.form.get("drag_order", "").strip()
    final_ids = []
    if drag_order:
        final_ids = [x for x in drag_order.split(",") if x]
    else:
        submitted_order = []
        for tid in track_ids:
            rank_str = request.form.get(f"rank_{tid}")
            if rank_str and rank_str.isdigit():
                submitted_order.append((tid, int(rank_str)))
        submitted_order.sort(key=lambda x: x[1])
        final_ids = [x[0] for x in submitted_order]

    final_ids = [x for x in final_ids if x in track_ids]
    if not final_ids:
        final_ids = track_ids

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    valid_tracks = [get_track(tid) for tid in track_ids if get_track(tid)]

    if ranking_mode == 'timeline':
        correct_ordered = sorted(valid_tracks, key=lambda t: t['year'])
    else:
        correct_ordered = sorted(valid_tracks, key=lambda t: t['popularity'])

    correct_ids_in_order = [t['id'] for t in correct_ordered]

    def count_correct_pairs(order_list):
        correct_count = 0
        total_pairs = 0
        for i in range(len(order_list)):
            for j in range(i+1, len(order_list)):
                total_pairs += 1
                tid_i = order_list[i]
                tid_j = order_list[j]
                idx_i = correct_ids_in_order.index(tid_i)
                idx_j = correct_ids_in_order.index(tid_j)
                if idx_i < idx_j:
                    correct_count += 1
        if total_pairs == 0:
            return 1.0
        return correct_count / total_pairs

    correctness_fraction = count_correct_pairs(final_ids)
    difficulty_str = request.form.get("difficulty", "3")
    try:
        difficulty_rating = int(difficulty_str)
    except:
        difficulty_rating = 3
    difficulty_rating = max(1, min(difficulty_rating, 5))
    rating_fraction = (difficulty_rating - 1) / 4
    outcome = 0.7 * correctness_fraction + 0.3 * rating_fraction

    user.update_elo('personalized', 'rank', outcome)
    db.session.commit()

    flash(f"Personalized rank done. Correctness={correctness_fraction:.2f}, Difficulty={difficulty_rating}, outcome={outcome:.2f}!", "info")
    session.pop('personalized_ranking_tracks', None)

    lines = get_buddy_personality_lines()
    if correctness_fraction > 0.8:
        session["buddy_message"] = random.choice(lines['correct'])
    else:
        session["buddy_message"] = random.choice(lines['wrong'])

    return redirect(url_for('quiz_bp.dashboard'))
