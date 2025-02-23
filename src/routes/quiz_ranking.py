# routes/quiz_ranking.py
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

def pick_spread_tracks(track_list, n=4, mode='timeline'):
    """
    Pick n tracks far apart in year or popularity.
    We sort all, then pick them with roughly even spacing.
    """
    if len(track_list) <= n:
        return track_list[:]

    if mode == 'timeline':
        sorted_all = sorted(track_list, key=lambda t: t['year'])
    else:
        sorted_all = sorted(track_list, key=lambda t: t['popularity'])

    step = max(1, len(sorted_all) // (n + 1))
    chosen = []
    idx = step
    while idx < len(sorted_all) and len(chosen) < n:
        chosen.append(sorted_all[idx])
        idx += step

    # If we still need more, pick random leftover
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
    Stores them in session['random_ranking_tracks'].
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

    # e.g. pick 2..4 random tracks
    n = random.choice([2, 3, 4][:max_n])

    # ensure unique popularity values for clarity
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

    # filter out anything not in track_ids
    final_ids = [x for x in final_ids if x in track_ids]
    if not final_ids:
        final_ids = track_ids

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    valid_tracks = [get_track(tid) for tid in track_ids if get_track(tid)]
    if ranking_mode == 'timeline':
        # ascending by year
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

    # Combine correctness with user-chosen difficulty rating
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

    session['random_ranking_results'] = {
        'approach': 'random',
        'final_ids': final_ids,
        'correct_ids': correct_ids_in_order,
        'correctness_fraction': correctness_fraction,
        'difficulty': difficulty_rating,
        'outcome': outcome,
    }

    return redirect(url_for('quiz_bp.ranking_results', approach='random'))


###############################################################################
# PERSONALIZED RANK
###############################################################################
from routes.quiz_personalized import build_personalization_scores, get_decade

@quiz_bp.route('/personalized_rank', methods=['GET'])
@require_login
def personalized_rank():
    """
    We'll pick a set of tracks to rank based on personalization logic,
    then display them in a separate "personalized_rank_drag.html" template.
    """
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks available. Please import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    # gather personalization data
    artist_scores, decade_scores, track_scores = build_personalization_scores(user)

    elo = user.personalized_rank_elo
    if elo < 1100:
        base_count = 3
    elif elo > 1400:
        base_count = 5
    else:
        base_count = 4

    difficulty = session.get('difficulty', 'normal')
    ranking_mode = session.get('ranking_mode', 'popularity')

    # pick half from top-missed artists/decades, half from leftover
    top_artists = sorted(artist_scores.keys(), key=lambda a: artist_scores[a], reverse=True)[:2]
    top_decades = sorted(decade_scores.keys(), key=lambda d: decade_scores[d], reverse=True)[:2]

    missed_candidates = []
    for t in ALL_TRACKS:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        if a in top_artists or d in top_decades:
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

    # use the new separate template:
    return render_template("personalized_rank_drag.html", tracks=chosen)


@quiz_bp.route('/personalized_rank_from_session')
@require_login
def personalized_rank_from_session():
    """
    Possibly let the user re-rank the personalized tracks already stored in session.
    """
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
        # fallback: check numeric rank inputs (if any)
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
        # ascending popularity
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

    user.update_elo('personalized', 'rank', outcome)
    db.session.commit()

    session.pop('personalized_ranking_tracks', None)  # done with them

    session['personalized_ranking_results'] = {
        'approach': 'personalized',
        'final_ids': final_ids,
        'correct_ids': correct_ids_in_order,
        'correctness_fraction': correctness_fraction,
        'difficulty': difficulty_rating,
        'outcome': outcome,
    }

    return redirect(url_for('quiz_bp.ranking_results', approach='personalized'))


###############################################################################
# RANKING RESULTS
###############################################################################
@quiz_bp.route('/ranking_results')
@require_login
def ranking_results():
    """
    Reads ?approach=(random|personalized) to decide which session key to load.
    """
    approach = request.args.get('approach', 'random')

    if approach == 'personalized':
        data = session.get('personalized_ranking_results')
    else:
        data = session.get('random_ranking_results')  # default

    if not data:
        flash(f"No ranking results found for approach='{approach}'.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    final_ids = data['final_ids']
    correct_ids = data['correct_ids']
    approach = data['approach']
    correctness_fraction = data['correctness_fraction']
    difficulty = data['difficulty']
    outcome = data['outcome']

    ranking_mode = session.get('ranking_mode', 'popularity')

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    length = max(len(final_ids), len(correct_ids))
    final_list = [get_track(tid) for tid in final_ids]
    correct_list = [get_track(tid) for tid in correct_ids]

    # pad if needed
    while len(final_list) < length:
        final_list.append(None)
    while len(correct_list) < length:
        correct_list.append(None)

    combined = []
    for i in range(length):
        combined.append((final_list[i], correct_list[i]))

    return render_template(
        "ranking_results.html",
        approach=approach,
        combined=combined,
        correctness=correctness_fraction,
        difficulty=difficulty,
        outcome=outcome,
        ranking_mode=ranking_mode
    )
