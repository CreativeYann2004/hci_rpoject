import random
import time
from datetime import datetime
from flask import (
    render_template, request, redirect, url_for,
    session, flash
)
from app import db
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

def time_decay_weight(guess_timestamp):
    """
    Example: a simple exponential decay.
    If a miss happened 'd' days ago, weight = 0.5^(d/7).
    (So each additional week halves the impact.)
    """
    now = datetime.utcnow()
    diff_days = (now - guess_timestamp).days
    return 0.5 ** (diff_days / 7.0)

def build_personalization_scores(user):
    """
    Examine the user's guess logs. 
    For each missed guess, increment:
      - The artist's "miss score"
      - The decade's "miss score"
      - The track's "miss score" (if you want to directly pick repeated misses)
    Weighted by recency (time_decay_weight).
    Returns dictionaries: (artist_scores, decade_scores, track_scores).
    """
    artist_scores = {}
    decade_scores = {}
    track_scores = {}

    # Gather all logs for user
    logs = user.guess_logs  # relationship from models.py
    for log in logs:
        if not log.is_correct:
            # Weighted by how recent the attempt was
            w = time_decay_weight(log.timestamp)
            # Find track in ALL_TRACKS
            track = next((t for t in ALL_TRACKS if t['id'] == log.track_id), None)
            if track:
                # Update artist
                a = track['artist'].lower()
                artist_scores[a] = artist_scores.get(a, 0) + w

                # Update decade
                d = get_decade(track['year'])
                decade_scores[d] = decade_scores.get(d, 0) + w

                # Update track
                track_scores[track['id']] = track_scores.get(track['id'], 0) + w

    return artist_scores, decade_scores, track_scores

def pick_personalized_track(user):
    """
    Weighted approach:
     1. Build scores for each artist & decade based on misses (weighted by recency).
     2. Combine the top few artists/decades into a 'pool' of candidate tracks.
     3. If no misses are found, fallback to random pick.
    """
    if not ALL_TRACKS:
        return None

    artist_scores, decade_scores, track_scores = build_personalization_scores(user)

    if not artist_scores and not decade_scores:
        # If user has no misses, fallback to random
        return random.choice(ALL_TRACKS)

    # Identify which artist(s) or decade(s) have the highest score
    # We'll say we focus on the top 3 artists and top 3 decades missed
    top_artists = sorted(artist_scores.keys(), key=lambda a: artist_scores[a], reverse=True)[:3]
    top_decades = sorted(decade_scores.keys(), key=lambda d: decade_scores[d], reverse=True)[:3]

    # Build candidate list
    candidates = []
    for t in ALL_TRACKS:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        if a in top_artists or d in top_decades:
            candidates.append(t)

    # If that yields no candidates (rare case), fallback
    if not candidates:
        return random.choice(ALL_TRACKS)

    # Weighted random: each track gets weight = sum of (artist_scores + decade_scores)
    # for its artist and decade.
    weighted_candidates = []
    for t in candidates:
        a = t['artist'].lower()
        d = get_decade(t['year'])
        weight = artist_scores.get(a, 0) + decade_scores.get(d, 0)
        # If track-based weighting is desired, add track_scores[t['id']] too
        weight += track_scores.get(t['id'], 0)
        # Minimum weight to avoid zero
        weight = max(weight, 0.1)
        weighted_candidates.append((t, weight))

    # Now pick based on these weights
    total_weight = sum(w for (_, w) in weighted_candidates)
    r = random.uniform(0, total_weight)
    cumulative = 0
    for (track_obj, w) in weighted_candidates:
        cumulative += w
        if r <= cumulative:
            return track_obj

    # Fallback
    return random.choice(weighted_candidates)[0]


########################
# Legacy adaptiveness  #
########################

def get_user_type_stats(user):
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    return session['type_misses']

def record_miss_for_type(question_type):
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    session['type_misses'][question_type] += 1

def pick_question_type_adaptive(user):
    """
    Simple logic: we weigh question types by how many times the user missed them.
    If they've missed year questions a lot, we choose 'year' more often, etc.
    """
    type_stats = get_user_type_stats(user)
    total_misses = sum(type_stats.values())
    if total_misses == 0:
        return random.choice(["artist", "title", "year"])

    weighted_pool = []
    for qtype in ["artist", "title", "year"]:
        count = type_stats.get(qtype, 0)
        weighted_pool += [qtype] * (count + 1)

    return random.choice(weighted_pool)


############################
# Personalized Guess Route #
############################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    # ELO-based snippet length example
    if user.personalized_guess_elo > 1400:
        snippet_length_seconds = 15
    elif user.personalized_guess_elo < 1100:
        snippet_length_seconds = 45
    else:
        snippet_length_seconds = 30
    session['snippet_length'] = snippet_length_seconds

    # Attempt advanced personalized pick
    chosen = pick_personalized_track(user)
    if not chosen:
        flash("No track available for personalization. Using random fallback.", "warning")
        chosen = random.choice(ALL_TRACKS)

    ctype = pick_question_type_adaptive(user)
    session["current_track_id"] = chosen["id"]
    session["challenge_type"] = ctype
    session['question_start_time'] = time.time()

    # Show extended hints if user is struggling
    show_extended_hints = (user.get_accuracy() < 0.5) or (user.personalized_guess_elo < 1300)
    generated_hints = generate_hints(chosen, ctype, user, is_personalized=show_extended_hints)

    lines = get_buddy_personality_lines()
    times_missed = user.get_missed_songs().count(chosen["id"])
    if times_missed >= 2:
        session["buddy_message"] = f"I see we've had trouble with this one {times_missed} times. Let's try again!"
    else:
        if user.get_accuracy() < 0.5:
            session["buddy_message"] = "It’s okay if you mess up—we’ll keep practicing!"
        else:
            session["buddy_message"] = random.choice(lines['start'])

    if generated_hints:
        session['buddy_hint'] = " | ".join(generated_hints)
    else:
        session.pop('buddy_hint', None)

    return render_template("personalized_version.html", song=chosen, feedback=None, hints=[])


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

    start_time = session.pop('question_start_time', None)
    time_taken = 0.0
    if start_time is not None:
        time_taken = time.time() - start_time

    challenge = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False

    def within_year_margin_adaptive(g, actual):
        # Tighter margin for higher ELO
        if user.personalized_guess_elo > 1400:
            return (g == actual)
        else:
            return abs(g - actual) <= 2

    if challenge == "artist":
        if guess_str == track["artist"].lower():
            guess_correct = True
    elif challenge == "title":
        if guess_str == track["title"].lower():
            guess_correct = True
    elif challenge == "year":
        if guess_str.isdigit():
            guess_year = int(guess_str)
            if within_year_margin_adaptive(guess_year, track["year"]):
                guess_correct = True

    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    lines = get_buddy_personality_lines()

    outcome = 1.0 if guess_correct else 0.0
    user.update_elo('personalized', 'guess', outcome)

    # Log attempt
    guess_log = GuessLog(
        user_id=user.id,
        track_id=track_id,
        question_type=challenge,
        is_correct=guess_correct,
        time_taken=time_taken,
        approach='personalized'
    )
    db.session.add(guess_log)

    if guess_correct:
        user.total_correct += 1
        feedback = f"Correct! {track['artist']} – {track['title']} ({track['year']})."
        if time_taken > 0:
            feedback += f" You took {time_taken:.1f} seconds."
        if track_id in missed_list:
            missed_list.remove(track_id)
        if time_taken < 5:
            session["buddy_message"] = "Lightning speed! Impressive!"
        else:
            session["buddy_message"] = random.choice(lines['correct'])
        session.pop('buddy_hint', None)
    else:
        feedback = f"Wrong! Correct: {track['artist']} – {track['title']} ({track['year']})."
        if time_taken > 0:
            feedback += f" Time taken: {time_taken:.1f} seconds."
        if track_id not in missed_list:
            missed_list.append(track_id)
        record_miss_for_type(challenge)
        session["buddy_message"] = random.choice(lines['wrong'])

    user.set_missed_songs(missed_list)
    db.session.commit()
    session["feedback"] = feedback
    return redirect(url_for('quiz_bp.personalized_feedback'))


@quiz_bp.route('/personalized_feedback')
@require_login
def personalized_feedback():
    fb = session.get("feedback")
    return render_template("personalized_version.html", song=None, feedback=fb, hints=[])


#####################################
#  Personalized Mistakes & Ranking  #
#####################################
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


"""
================================================================================
SAMPLE USER STUDY & SIGNIFICANCE TESTING OUTLINE (comments only)
--------------------------------------------------------------------------------
1. Participants:
   - Recruit >=12 participants (students, colleagues, random test users).
   - They will try both versions: random and personalized.

2. Tasks:
   - Each participant does a set of guess tasks and ranking tasks in each version.
   - We gather objective data: 
     * Time to guess each track,
     * Whether guess is correct,
     * Final ranking accuracy (pairwise correctness),
     * ELO changes, etc.

3. Subjective Data:
   - After each version, participants fill in:
     * System Usability Scale (SUS) or standard usability form,
     * Flow State Scale (how 'in the zone' they felt),
     * Or Russell’s Circumplex model (arousal, valence).

4. Metrics to Analyze:
   - Objective: average time, accuracy, # of misses, ranking correctness fraction, etc.
   - Subjective: SUS scores, Flow State scores, etc.
   - Emotional data: e.g., self-reported or observed.

5. Hypotheses:
   - H0: "There is no difference in average guess time between random and personalized."
   - H1: "Personalized approach yields a lower (faster) guess time on average."

6. Statistical Test:
   - For each user, compute their average guess time (random vs. personalized).
   - Use a paired t-test:
     * t, p = ...
     * alpha=0.05
   - If p < 0.05 => reject H0 => conclude personalized version is significantly faster.

7. Similarly for ranking correctness or enjoyment rating:
   - H0: "No difference in ranking correctness."
   - H1: "Personalized approach yields higher correctness."
   - Compare average correctness across all participants, use a Wilcoxon or t-test.

8. Store Data & Materials:
   - Put user logs (GuessLog DB), questionnaires, raw data in an appendix or shared drive.

================================================================================
"""
