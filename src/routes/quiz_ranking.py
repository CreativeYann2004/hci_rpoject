import random
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash
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

###############################################################################
# HELPER: "CLOSENESS" & "SPREAD"
###############################################################################
def pick_close_tracks(track_list, n=4, mode='timeline'):
    """
    Pick n tracks that are close in year or popularity.
    We choose an anchor track, then pick the nearest neighbors.
    """
    if len(track_list) <= n:
        return track_list[:]

    anchor = random.choice(track_list)
    if mode == 'timeline':
        anchor_val = anchor['year']
        sorted_by_diff = sorted(track_list, key=lambda t: abs(t['year'] - anchor_val))
    else:
        anchor_val = anchor['popularity']
        sorted_by_diff = sorted(track_list, key=lambda t: abs(t['popularity'] - anchor_val))

    chosen = []
    for t in sorted_by_diff:
        if t not in chosen:
            chosen.append(t)
        if len(chosen) >= n:
            break
    return chosen

def pick_spread_tracks(track_list, n=4, mode='timeline', elo=1200):
    """
    Pick n tracks far apart in year or popularity.
    We sort all, then pick them with roughly even spacing.
    Adjust the spread based on the user's ELO.
    """
    if len(track_list) <= n:
        return track_list[:]

    if mode == 'timeline':
        sorted_all = sorted(track_list, key=lambda t: t['year'])
    else:
        sorted_all = sorted(track_list, key=lambda t: t['popularity'])

    # Calculate the spread factor based on ELO
    spread_factor = max(0.5, min(2.0, 1.0 + (elo - 1200) / 600.0))

    step = max(1, int(len(sorted_all) // (n * spread_factor)))
    chosen = []
    idx = step
    while idx < len(sorted_all) and len(chosen) < n:
        chosen.append(sorted_all[idx])
        idx += step

    while len(chosen) < n:
        leftover = [x for x in sorted_all if x not in chosen]
        if not leftover:
            break
        chosen.append(random.choice(leftover))
    return chosen

###############################################################################
# RANDOM RANK
###############################################################################
@quiz_bp.route('/random_rank', methods=['GET'])
@require_login
def random_rank():
    """
    Presents a random subset of tracks for ranking.
    """
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    max_n = min(len(ALL_TRACKS), 4)
    if max_n < 2:
        flash("Not enough tracks to rank!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    n = random.choice([2, 3, 4][:max_n])

    sample_tracks = []
    while len(sample_tracks) < n:
        track = random.choice(ALL_TRACKS)
        if track not in sample_tracks and all(t['popularity'] != track['popularity'] for t in sample_tracks):
            sample_tracks.append(track)

    session['random_ranking_tracks'] = [t['id'] for t in sample_tracks]
    session['ranking_mode'] = session.get('ranking_mode', 'popularity')  # default

    return render_template("random_rank_drag.html", tracks=sample_tracks)

@quiz_bp.route('/submit_random_rank', methods=['POST'])
@require_login
def submit_random_rank():
    user = current_user()
    ranking_mode = session.get('ranking_mode', 'popularity')

    track_ids = session.get('random_ranking_tracks', [])
    if not track_ids:
        flash("No tracks to rank! Please start again.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    final_order = request.form.get("final_order", "").strip()
    final_ids = [x for x in final_order.split(",") if x]

    # Ensure only the track_ids we offered are used
    final_ids = [x for x in final_ids if x in track_ids]
    if not final_ids:
        final_ids = track_ids

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    valid_tracks = [get_track(tid) for tid in track_ids if get_track(tid)]
    if ranking_mode == 'timeline':
        correct_ordered = sorted(valid_tracks, key=lambda t: t['year'])
    else:
        # descending by popularity => highest popularity is rank 1
        correct_ordered = sorted(valid_tracks, key=lambda t: t['popularity'], reverse=True)

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
        return correct_count / total_pairs if total_pairs else 1.0

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

    # Store everything in session['random_ranking_results']
    session["random_ranking_results"] = {
        "approach": "random",
        "ranking_mode": ranking_mode,
        "final_ids": final_ids,
        "correct_ids": correct_ids_in_order,
        "correctness_fraction": correctness_fraction,
        "difficulty": difficulty_rating,
        "outcome": outcome
    }

    return redirect(url_for('quiz_bp.ranking_results', approach='random'))

###############################################################################
# PERSONALIZED RANK
###############################################################################
from routes.quiz_personalized import build_personalization_scores, get_decade

@quiz_bp.route('/personalized_rank', methods=['GET'])
@require_login
def personalized_rank():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks available. Please import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    artist_scores, decade_scores, track_scores, genre_scores = build_personalization_scores(user)

    elo = user.personalized_rank_elo
    if elo < 1100:
        base_count = 3
    elif elo > 1400:
        base_count = 5
    else:
        base_count = 4

    difficulty = session.get('difficulty', 'normal')
    ranking_mode = session.get('ranking_mode', 'popularity')

    def top_n_from_dict(d, n=2):
        return sorted(d.keys(), key=lambda k: d[k], reverse=True)[:n]

    top_artists = top_n_from_dict(artist_scores)
    top_decades = top_n_from_dict(decade_scores)
    top_genres = top_n_from_dict(genre_scores)

    missed_candidates = []
    for t in ALL_TRACKS:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        gset = set(t.get('genre_list', []))
        if a in top_artists or d in top_decades or gset.intersection(top_genres):
            missed_candidates.append(t)

    chosen = []
    half = max(1, base_count // 2)
    random.shuffle(missed_candidates)
    while len(chosen) < half and missed_candidates:
        chosen.append(missed_candidates.pop())

    leftover = [t for t in ALL_TRACKS if t not in chosen]
    needed = base_count - len(chosen)
    if needed < 0:
        needed = 0

    from .quiz_ranking import pick_close_tracks, pick_spread_tracks

    if needed > 0:
        if difficulty == 'hard':
            cluster = pick_close_tracks(leftover, needed, mode=ranking_mode)
            chosen.extend(cluster)
        elif difficulty == 'easy':
            cluster = pick_spread_tracks(leftover, needed, mode=ranking_mode)
            chosen.extend(cluster)
        else:
            if len(leftover) <= needed:
                chosen.extend(leftover)
            else:
                chosen.extend(random.sample(leftover, needed))

    if len(chosen) < 2:
        flash("Not enough tracks to do a personalized ranking!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    session['personalized_ranking_tracks'] = [c['id'] for c in chosen]

    return render_template("personalized_rank_drag.html", tracks=chosen, elo=elo)

@quiz_bp.route('/personalized_rank_from_session')
@require_login
def personalized_rank_from_session():
    track_ids = session.get('personalized_ranking_tracks', [])
    if not track_ids:
        flash("No personalized tracks in session. Starting a new selection.", "info")
        return redirect(url_for('quiz_bp.personalized_rank'))

    final_tracks = [t for t in ALL_TRACKS if t['id'] in track_ids]
    if len(final_tracks) < 2:
        flash("Need at least 2 tracks to rank!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])
    return render_template("personalized_rank_drag.html", tracks=final_tracks)

@quiz_bp.route('/submit_personalized_rank', methods=['POST'])
@require_login
def submit_personalized_rank():
    user = current_user()
    ranking_mode = session.get('ranking_mode', 'popularity')

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
        # descending popularity
        correct_ordered = sorted(valid_tracks, key=lambda t: t['popularity'], reverse=True)

    correct_ids_in_order = [t['id'] for t in correct_ordered]

    def count_correct_pairs(order_list):
        correct_count = 0
        total_pairs = 0
        for i in range(len(order_list)):
            for j in range(i + 1, len(order_list)):
                total_pairs += 1
                tid_i = order_list[i]
                tid_j = order_list[j]
                idx_i = correct_ids_in_order.index(tid_i)
                idx_j = correct_ids_in_order.index(tid_j)
                if idx_i < idx_j:
                    correct_count += 1
        return correct_count / total_pairs if total_pairs else 1.0

    correctness_fraction = count_correct_pairs(final_ids)

    difficulty_str = request.form.get("difficulty", "3")
    try:
        difficulty_rating = int(difficulty_str)
    except:
        difficulty_rating = 3
    difficulty_rating = max(1, min(difficulty_rating, 5))
    rating_fraction = (difficulty_rating - 1) / 4

    outcome = 0.7 * correctness_fraction + 0.3 * rating_fraction

    old_elo = user.personalized_rank_elo
    user.update_elo('personalized', 'rank', outcome)
    new_elo = user.personalized_rank_elo
    elo_change = new_elo - old_elo

    db.session.commit()

    session.pop('personalized_ranking_tracks', None)

    # Store everything in session['personalized_ranking_results']
    session["personalized_ranking_results"] = {
        "approach": "personalized",
        "ranking_mode": ranking_mode,
        "final_ids": final_ids,
        "correct_ids": correct_ids_in_order,
        "correctness_fraction": correctness_fraction,
        "difficulty": difficulty_rating,
        "outcome": outcome,
        "new_elo": new_elo,
        "elo_change": elo_change,
    }

    return redirect(url_for('quiz_bp.ranking_results', approach='personalized'))

###############################################################################
# RANKING RESULTS
###############################################################################
@quiz_bp.route('/ranking_results/<approach>', methods=['GET'])
@require_login
def ranking_results(approach):
    """
    Displays the final ranking results for either random or personalized approach.
    """
    if approach == "personalized":
        results = session.get("personalized_ranking_results")
    else:
        results = session.get("random_ranking_results")

    if not results:
        flash(f"No ranking results found for approach='{approach}'.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    final_ids = results.get("final_ids", [])
    correct_ids = results.get("correct_ids", [])

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t["id"] == tid), None)

    length = max(len(final_ids), len(correct_ids))
    final_list = [get_track(tid) for tid in final_ids]
    correct_list = [get_track(tid) for tid in correct_ids]

    while len(final_list) < length:
        final_list.append(None)
    while len(correct_list) < length:
        correct_list.append(None)

    combined = list(zip(final_list, correct_list))

    return render_template(
        "ranking_results.html",
        results=results,
        combined=combined
    )
