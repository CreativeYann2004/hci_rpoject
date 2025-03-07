import random
import time
from datetime import datetime
from flask import (
    render_template, request, redirect, url_for,
    session, flash
)
from src.app import db
from models import User, GuessLog
from routes.quiz_base import (
    quiz_bp, ALL_TRACKS, RANDOM_MUSIC_FACTS,
    current_user, require_login, get_buddy_personality_lines,
    generate_hints
)

############################
# Advanced Personalization #
############################
def get_decade(year):
    return (year // 10) * 10

def time_decay_weight(guess_timestamp, times_missed=1):
    """
    Exponential decay based on how many days ago the guess happened.
    Each additional week halves the impact.
    
    Adds a small 'spaced repetition' factor for repeated misses:
      weight *= (1 + 0.1 * (times_missed - 1)).
    """
    now = datetime.utcnow()
    diff_days = (now - guess_timestamp).days
    base_weight = 0.5 ** (diff_days / 7.0)

    # Spaced repetition factor: track repeated misses
    spaced_factor = 1.0 + 0.1 * (times_missed - 1)
    return base_weight * spaced_factor

def build_personalization_scores(user):
    """
    Examine the user's guess logs to see which artists, decades, or tracks
    gave them trouble. Weighted by recency + repeated misses + user’s favorite_genres if any.
    """
    artist_scores = {}
    decade_scores = {}
    track_scores = {}
    genre_scores = {}

    prefs = user.get_preferences()
    fav_genres = prefs.get('favorite_genres', [])

    for log in user.guess_logs:
        if not log.is_correct:
            # How many times the user missed THIS track so far?
            times_missed_for_that_track = user.get_incorrect_count_for_track(log.track_id)

            w = time_decay_weight(log.timestamp, times_missed_for_that_track)
            track = next((t for t in ALL_TRACKS if t['id'] == log.track_id), None)
            if track:
                a = track['artist'].lower()
                d = get_decade(track['year'])
                artist_scores[a] = artist_scores.get(a, 0) + w
                decade_scores[d] = decade_scores.get(d, 0) + w
                track_scores[track['id']] = track_scores.get(track['id'], 0) + w

                # If track has genre_list, weigh these
                for g in track.get("genre_list", []):
                    # 2x weighting if the user labeled that genre as a "favorite"
                    multiplier = 2.0 if g in fav_genres else 1.0
                    genre_scores[g] = genre_scores.get(g, 0) + w * multiplier

    return artist_scores, decade_scores, track_scores, genre_scores

def pick_personalized_track(user):
    """
    Weighted approach: pick from the top missed items more frequently.
    Fallback to random if no misses.
    """
    if not ALL_TRACKS:
        return None

    artist_scores, decade_scores, track_scores, genre_scores = build_personalization_scores(user)

    # Fallback if user has no misses
    if not artist_scores and not decade_scores and not track_scores and not genre_scores:
        return random.choice(ALL_TRACKS)

    def top_n(d, n=3):
        return sorted(d.keys(), key=lambda k: d[k], reverse=True)[:n]

    top_artists = top_n(artist_scores)
    top_decades = top_n(decade_scores)
    top_genres = top_n(genre_scores)
    top_tracks = top_n(track_scores)

    # Build candidate set
    candidates = []
    for t in ALL_TRACKS:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        tid = t['id']
        tgenres = t.get('genre_list', [])
        if (a in top_artists) or (d in top_decades) \
           or (tid in top_tracks) or (set(tgenres).intersection(top_genres)):
            candidates.append(t)

    if not candidates:
        return random.choice(ALL_TRACKS)

    # Weighted random among candidates
    weighted_candidates = []
    # Rebuild the personalization scores once (to not re-run build inside loop)
    artist_scores_, decade_scores_, track_scores_, genre_scores_ = build_personalization_scores(user)
    
    for t in candidates:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        tid = t['id']
        w = (artist_scores_.get(a, 0)
             + decade_scores_.get(d, 0)
             + track_scores_.get(tid, 0))
        for g in t.get('genre_list', []):
            w += genre_scores_.get(g, 0)

        if w < 0.1:
            w = 0.1
        weighted_candidates.append((t, w))

    total_weight = sum(wc[1] for wc in weighted_candidates)
    r = random.uniform(0, total_weight)
    cumulative = 0
    for (track_obj, w) in weighted_candidates:
        cumulative += w
        if r <= cumulative:
            return track_obj
    return random.choice(weighted_candidates)[0]

########################################
# Additional adaptive question type logic
########################################
def get_user_type_stats(user):
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    if 'quick_misses' not in session:
        session['quick_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    return session['type_misses'], session['quick_misses']

def record_miss_for_type(qtype, time_taken=0.0):
    type_misses, quick_misses = get_user_type_stats(current_user())
    type_misses[qtype] += 1
    # if user misses in under 5s, count it as a "quick" guess (maybe typed randomly)
    if time_taken < 5.0:
        quick_misses[qtype] += 1
    session['type_misses'] = type_misses
    session['quick_misses'] = quick_misses

def pick_question_type_adaptive(user):
    """
    Adaptive logic: 
      - Base weighting uses how many times user missed each question type.
      - Ensures "year" has at least a 20% chance overall 
        (so it doesn't get overshadowed by artist/title misses).
    """
    type_misses, quick_misses = get_user_type_stats(user)
    qtypes = ["artist", "title", "year"]

    # Build base weights
    weights = {}
    for qt in qtypes:
        base_val = 1.0  # always start with 1 so each type has a chance
        w = base_val + type_misses[qt] + 0.5 * quick_misses[qt]
        # If you want to slightly boost year in general, you could do:
        # if qt == 'year':
        #     w += 0.3
        weights[qt] = w

    total_w = sum(weights.values())
    # Enforce: year should be at least 20% chance
    current_year_frac = weights['year'] / total_w
    desired_min_frac = 0.2  # 20%
    if current_year_frac < desired_min_frac:
        # Increase the weight of 'year' so it meets 20% threshold
        shortfall = (desired_min_frac * total_w) - weights['year']
        weights['year'] += shortfall
        # recalc total_w
        total_w = sum(weights.values())

    # Weighted random pick
    r = random.uniform(0, total_w)
    cumul = 0
    for qt in qtypes:
        cumul += weights[qt]
        if r <= cumul:
            return qt
    return random.choice(qtypes)  # fallback

############################
# HELPER for stats panel
############################
def get_top_missed_artists(user, n=5):
    """
    Return a list of (artist_name, score) for the top N missed artists.
    """
    artist_scores, _, _, _ = build_personalization_scores(user)
    sorted_artists = sorted(artist_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_artists[:n]

def get_elo_history(user, limit=5):
    """
    Return a small list of (label, value) for the user’s last <limit> 
    personalized guess logs, capturing ELO after each guess (approx).
    """
    relevant = [gl for gl in user.guess_logs if gl.approach=='personalized']
    relevant.sort(key=lambda x: x.timestamp)
    logs = []
    temp_elo = 1200
    for g in relevant:
        outcome = 1.0 if g.is_correct else 0.0
        old_elo = temp_elo
        new_elo = old_elo + 32*(outcome - 0.5)
        temp_elo = round(new_elo)
        label_date = g.timestamp.strftime('%m/%d')
        logs.append({"label": label_date, "value": temp_elo})
    return logs[-limit:]

def get_accuracy_history(user, limit=5):
    """
    Return a list of 0..1 accuracy for the user’s last N personalized guesses, cumulatively.
    """
    relevant = [gl for gl in user.guess_logs if gl.approach=='personalized']
    relevant.sort(key=lambda x: x.timestamp)
    if not relevant:
        return []
    correct_count = 0
    total_so_far = 0
    results = []
    for g in relevant:
        total_so_far += 1
        if g.is_correct:
            correct_count += 1
        results.append(correct_count / total_so_far)
    return results[-limit:]

############################
# Personalized Guess
############################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    # ELO-based snippet logic
    if user.personalized_guess_elo > 1400:
        snippet = 15
    elif user.personalized_guess_elo < 1100:
        snippet = 45
    else:
        snippet = 30
    session['snippet_length'] = snippet

    chosen = pick_personalized_track(user)
    if not chosen:
        flash("No track for personalization; fallback random.", "warning")
        chosen = random.choice(ALL_TRACKS)

    ctype = pick_question_type_adaptive(user)
    session["current_track_id"] = chosen["id"]
    session["challenge_type"] = ctype
    session['question_start_time'] = time.time()

    lines = get_buddy_personality_lines()
    times_missed = user.get_missed_songs().count(chosen["id"])
    if times_missed >= 2:
        session["buddy_message"] = f"We've struggled with this track {times_missed} times. Let's do it!"
    else:
        if user.get_accuracy() < 0.5:
            session["buddy_message"] = "It’s okay if you mess up—we’ll keep practicing!"
        else:
            session["buddy_message"] = random.choice(lines['start'])

    # Hints
    hints = generate_hints(chosen, ctype, user, is_personalized=True)
    if hints:
        session['buddy_hint'] = " | ".join(hints)
    else:
        session.pop('buddy_hint', None)

    # Prepare side panel data
    top_missed = get_top_missed_artists(user, n=5)
    elo_hist = get_elo_history(user, limit=6)
    acc_hist = get_accuracy_history(user, limit=6)

    return render_template(
        "personalized_version.html",
        song=chosen,
        feedback=None,
        hints=[],
        user=user,
        top_missed_artists=top_missed,
        elo_history=elo_hist,
        acc_history=acc_hist
    )

@quiz_bp.route('/submit_guess_personalized', methods=['POST'])
@require_login
def submit_guess_personalized():
    user = current_user()
    track_id = session.get("current_track_id")
    if not track_id:
        flash("No active track. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    track = next((t for t in ALL_TRACKS if t["id"] == track_id), None)
    if not track:
        flash("Track not found. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    start_t = session.pop('question_start_time', None)
    time_taken = time.time() - start_t if start_t else 0.0

    ctype = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False

    # More dynamic margin for year based on ELO
    def within_year_margin_adaptive(g, actual, elo):
        if elo < 1100:
            margin = 3
        elif elo < 1400:
            margin = 2
        else:
            margin = 0  # must match exactly
        return abs(g - actual) <= margin

    # Evaluate correctness
    if ctype == "artist":
        if guess_str == track["artist"].lower():
            guess_correct = True
    elif ctype == "title":
        if guess_str == track["title"].lower():
            guess_correct = True
    elif ctype == "year":
        if guess_str.isdigit():
            gyear = int(guess_str)
            if within_year_margin_adaptive(gyear, track["year"], user.personalized_guess_elo):
                guess_correct = True

    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    lines = get_buddy_personality_lines()

    # ELO
    outcome = 1.0 if guess_correct else 0.0
    old_elo = user.personalized_guess_elo
    user.update_elo('personalized', 'guess', outcome)
    new_elo = user.personalized_guess_elo
    elo_diff = new_elo - old_elo

    # Track whether a buddy hint was shown
    buddy_hints_shown = 1 if 'buddy_hint' in session else 0

    # Log guess
    guess_log = GuessLog(
        user_id=user.id,
        track_id=track_id,
        question_type=ctype,
        is_correct=guess_correct,
        time_taken=time_taken,
        approach='personalized',
        buddy_hints_shown=buddy_hints_shown
    )
    db.session.add(guess_log)

    # Build feedback
    if guess_correct:
        user.total_correct += 1
        fb = f"Correct! {track['artist']} – {track['title']} ({track['year']})."
        if time_taken > 0:
            fb += f" You took {time_taken:.1f} seconds."
        if track_id in missed_list:
            missed_list.remove(track_id)

        if time_taken < 5:
            session["buddy_message"] = "Lightning speed! Impressive!"
        else:
            session["buddy_message"] = random.choice(lines['correct'])
        session.pop('buddy_hint', None)
    else:
        fb = f"Wrong! Correct: {track['artist']} – {track['title']} ({track['year']})."
        if time_taken > 0:
            fb += f" Time taken: {time_taken:.1f} seconds."
        if track_id not in missed_list:
            missed_list.append(track_id)
        record_miss_for_type(ctype, time_taken)
        session["buddy_message"] = random.choice(lines['wrong'])

    user.set_missed_songs(missed_list)
    db.session.commit()

    # Store feedback, elo_diff
    session["feedback"] = fb
    session["elo_diff"] = elo_diff

    return redirect(url_for('quiz_bp.personalized_feedback'))

##################################################
# Helper: Next Buddy Stage Threshold Calculation #
##################################################
def get_next_buddy_threshold(current_elo):
    """
    Returns the next threshold ELO and name of stage, or None if at highest stage.
    """
    stages = [
        ("Bronze", 1100),
        ("Silver", 1300),
        ("Gold",   1500),
        ("Diamond", float("inf")) 
    ]
    for stage_name, threshold in stages:
        if current_elo < threshold:
            return threshold, stage_name
    return None, None  # Already at max

@quiz_bp.route('/personalized_feedback')
@require_login
def personalized_feedback():
    fb = session.get("feedback")
    user = current_user()
    diff = session.pop('elo_diff', 0)

    # Calculate how many points to next threshold
    current_elo = user.personalized_guess_elo if user else 1000
    next_threshold, next_stage_name = get_next_buddy_threshold(current_elo)
    points_to_next = 0
    if next_threshold and next_threshold != float('inf'):
        points_to_next = next_threshold - current_elo

    # Prepare side panel data again
    top_missed = get_top_missed_artists(user, n=5)
    elo_hist = get_elo_history(user, limit=6)
    acc_hist = get_accuracy_history(user, limit=6)

    return render_template(
        "personalized_version.html",
        song=None,
        feedback=fb,
        hints=[],
        user=user,
        elo_diff=diff,
        points_to_next=points_to_next,
        next_stage_name=next_stage_name,
        top_missed_artists=top_missed,
        elo_history=elo_hist,
        acc_history=acc_hist
    )

#####################################
#  Personalized Mistakes & Ranking  #
#####################################
@quiz_bp.route('/personalized_mistakes')
@require_login
def personalized_mistakes():
    user = current_user()
    missed_ids = user.get_missed_songs()
    if not missed_ids:
        flash("You currently have no missed songs!", "info")
        return redirect(url_for('quiz_bp.dashboard'))

    missed_tracks = [t for t in ALL_TRACKS if t['id'] in missed_ids]
    missed_tracks.sort(key=lambda x: x['year'])
    return render_template("personalized_mistakes.html", missed_tracks=missed_tracks)

@quiz_bp.route('/rank_mistakes_only', methods=['POST'])
@require_login
def rank_mistakes_only():
    user = current_user()
    missed_ids = user.get_missed_songs()
    missed_tracks = [t for t in ALL_TRACKS if t['id'] in missed_ids]
    if len(missed_tracks) < 2:
        flash("Need at least 2 missed tracks to rank them!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    session['personalized_ranking_tracks'] = [x['id'] for x in missed_tracks]
    flash("Focusing on your missed songs. Good luck!", "info")
    return redirect(url_for('quiz_bp.personalized_rank_from_session'))
