# quiz_ranking.py

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

###############################################################################
# HELPER FUNCTIONS: "CLOSENESS" & "SPREAD" (for easy/hard track picks)
###############################################################################
def pick_close_tracks(track_list, n=4, mode='timeline'):
    """
    Pick `n` tracks that are close in year or popularity (making it "harder").
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
    Pick `n` tracks far apart in year or popularity (making it "easier").
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

    # If still not enough, pick randomly from leftover
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
    
    # Ensure unique popularity values
    sample_tracks = []
    while len(sample_tracks) < n:
        track = random.choice(ALL_TRACKS)
        if track not in sample_tracks and all(t['popularity'] != track['popularity'] for t in sample_tracks):
            sample_tracks.append(track)

    session['ranking_tracks'] = [t['id'] for t in sample_tracks]
    session['ranking_mode'] = session.get('ranking_mode', 'popularity')  # Ensure default is 'popularity'

    return render_template("random_rank_drag.html", tracks=sample_tracks)


@quiz_bp.route('/submit_random_rank', methods=['POST'])
@require_login
def submit_random_rank():
    user = current_user()
    ranking_mode = session.get('ranking_mode', 'popularity')
    track_ids = session.get('ranking_tracks', [])
    if not track_ids:
        flash("No tracks to rank! Please start again.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    final_order = request.form.get("final_order", "").strip()
    final_ids = [x for x in final_order.split(",") if x]

    final_ids = [x for x in final_ids if x in track_ids]
    if not final_ids:
        final_ids = track_ids  # fallback

    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    valid_tracks = [get_track(tid) for tid in track_ids if get_track(tid)]
    # Sort them by correct order
    if ranking_mode == 'timeline':
        correct_ordered = sorted(valid_tracks, key=lambda t: t['year'])
    else:
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

    # Instead of going directly to the dashboard, store final results in session
    # Then redirect to a results screen
    session['ranking_results'] = {
        'approach': 'random',
        'final_ids': final_ids,
        'correct_ids': correct_ids_in_order,
        'correctness_fraction': correctness_fraction,
        'difficulty': difficulty_rating,
        'outcome': outcome,
    }

    return redirect(url_for('quiz_bp.ranking_results'))


###############################################################################
# PERSONALIZED MISTAKES SCREEN
###############################################################################
@quiz_bp.route('/personalized_mistakes')
@require_login
def personalized_mistakes():
    """
    Shows a table of the user's missed songs, with an option
    to do a new round focusing only on those missed items.
    """
    user = current_user()
    missed_ids = user.get_missed_songs()
    if not missed_ids:
        flash("You currently have no missed songs!", "info")
        return redirect(url_for('quiz_bp.dashboard'))

    missed_tracks = [t for t in ALL_TRACKS if t['id'] in missed_ids]
    # just sorted by year for neatness
    missed_tracks.sort(key=lambda x: x['year'])
    return render_template("personalized_mistakes.html", missed_tracks=missed_tracks)


@quiz_bp.route('/rank_mistakes_only', methods=['POST'])
@require_login
def rank_mistakes_only():
    """
    Direct route to do a personalized ranking with only missed tracks.
    """
    user = current_user()
    missed_ids = user.get_missed_songs()
    missed_tracks = [t for t in ALL_TRACKS if t['id'] in missed_ids]
    if len(missed_tracks) < 2:
        flash("Need at least 2 missed tracks to rank them!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    session['personalized_ranking_tracks'] = [x['id'] for x in missed_tracks]
    flash("Focusing on your missed songs. Good luck!", "info")
    return redirect(url_for('quiz_bp.personalized_rank_from_session'))


###############################################################################
# PERSONALIZED RANK ROUTES
###############################################################################
@quiz_bp.route('/personalized_rank', methods=['GET'])
@require_login
def personalized_rank():
    """
    Personalized ranking that factors in user ELO, difficulty (close/spread),
    and missed songs.
    """
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks available. Please import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['rank'])

    # ELO-based count
    elo = user.personalized_rank_elo
    if elo < 1100:
        base_count = 3
    elif elo > 1400:
        base_count = 5
    else:
        base_count = 4

    difficulty = session.get('difficulty', 'normal')  # 'easy', 'normal', 'hard'
    ranking_mode = session.get('ranking_mode', 'popularity')  # Ensure default is 'popularity'

    missed_list = user.get_missed_songs()
    missed_candidates = [t for t in ALL_TRACKS if t['id'] in missed_list]

    chosen = []
    # Possibly pick half from missed if we have them
    if missed_candidates and random.random() < 0.5:
        half = max(1, base_count // 2)
        random.shuffle(missed_candidates)
        while len(chosen) < half and missed_candidates:
            chosen.append(missed_candidates.pop())

    needed = base_count - len(chosen)
    if needed < 0:
        needed = 0

    leftover = [t for t in ALL_TRACKS if t not in chosen]
    if needed > 0:
        if difficulty == 'hard':
            cluster = pick_close_tracks(leftover, needed, mode=ranking_mode)
            chosen.extend(cluster)
        elif difficulty == 'easy':
            cluster = pick_spread_tracks(leftover, needed, mode=ranking_mode)
            chosen.extend(cluster)
        else:
            # normal => random
            if len(leftover) <= needed:
                chosen.extend(leftover)
            else:
                chosen.extend(random.sample(leftover, needed))

    if len(chosen) < 2:
        flash("Not enough tracks to do a personalized ranking!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    session['personalized_ranking_tracks'] = [c['id'] for c in chosen]
    return render_template("random_rank_drag.html", tracks=chosen)


@quiz_bp.route('/personalized_rank_from_session')
@require_login
def personalized_rank_from_session():
    """
    If we already have 'personalized_ranking_tracks' in session (e.g. user picks "rank mistakes only"),
    go straight to the DnD screen with those tracks.
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
    return render_template("random_rank_drag.html", tracks=final_tracks)


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

    session.pop('personalized_ranking_tracks', None)

    # Store a "results summary" in session
    session['ranking_results'] = {
        'approach': 'personalized',
        'final_ids': final_ids,
        'correct_ids': correct_ids_in_order,
        'correctness_fraction': correctness_fraction,
        'difficulty': difficulty_rating,
        'outcome': outcome,
    }

    return redirect(url_for('quiz_bp.ranking_results'))


###############################################################################
# NEW: RANKING RESULTS SCREEN
###############################################################################
@quiz_bp.route('/ranking_results')
@require_login
def ranking_results():
    """
    Shows a summary of the final vs. correct ranking,
    highlighting which positions are mistakes,
    plus a button to start a new round.
    """
    data = session.get('ranking_results')
    if not data:
        flash("No ranking results found!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    final_ids = data['final_ids']
    correct_ids = data['correct_ids']
    approach = data['approach']  # 'random' or 'personalized'
    correctness_fraction = data['correctness_fraction']
    difficulty = data['difficulty']
    outcome = data['outcome']

    # Let's also fetch the ranking_mode from session if we want to display it
    ranking_mode = session.get('ranking_mode', 'popularity')  # or 'popularity'

    # Helper to get the track object
    def get_track(tid):
        return next((t for t in ALL_TRACKS if t['id'] == tid), None)

    length = max(len(final_ids), len(correct_ids))
    final_list = [get_track(tid) for tid in final_ids]
    correct_list = [get_track(tid) for tid in correct_ids]

    # Pad so both lists have the same length, if you want them side-by-side
    final_list += [None] * (length - len(final_list))
    correct_list += [None] * (length - len(correct_list))

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
