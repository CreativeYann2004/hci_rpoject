import random
import time
import re
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session
)
from app import db
from models import User
from routes.quiz_base import (
    quiz_bp, ALL_TRACKS, RANDOM_MUSIC_FACTS,
    current_user, require_login, get_buddy_personality_lines
)

###################################################################
# HELPER: Track the user’s per-type performance
###################################################################
def get_user_type_stats(user):
    """
    Return a dictionary like:
      {
        'artist_misses': <int>,
        'title_misses': <int>,
        'year_misses': <int>
      }
    We'll store it in user.missed_song_types (a JSON field) or
    do a quick hack: store in user.missed_songs if you don’t want
    to expand the model. Alternatively, store in session.
    """
    # If your User model doesn’t yet have a field for storing these stats,
    # you could store them in session or an extra JSON column.
    # For demonstration, let’s store them in session (simple approach):

    # Example structure in session:
    # session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    return session['type_misses']

def record_miss_for_type(question_type):
    """
    Increments the miss count for a particular question_type in session.
    """
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    session['type_misses'][question_type] += 1


###################################################################
# PICK QUESTION TYPE: Weighted by user’s misses
###################################################################
def pick_question_type_adaptive(user):
    """
    Example approach: Weighted random to pick the question type the user
    struggles with most. If user is equally bad at everything, do random.
    If user is good at all, do random as well.
    """
    type_stats = get_user_type_stats(user)
    # E.g. { 'artist': 4, 'title': 10, 'year': 7 }
    # The bigger the count, the more we want to ask them that question.

    # If a user has 0 misses in everything, we can just pick random
    total_misses = sum(type_stats.values())
    if total_misses == 0:
        return random.choice(["artist", "title", "year"])

    # Weighted random approach
    # Build a weighted list with each question type repeated N times = (misses + 1)
    # The +1 ensures if a user has 0 misses for some type, it still can appear.
    weighted_pool = []
    for qtype in ["artist", "title", "year"]:
        # E.g. if user missed 'year' 5 times => add 6 copies of 'year'
        count = type_stats.get(qtype, 0)
        weighted_pool += [qtype] * (count + 1)

    return random.choice(weighted_pool)


###################################################################
# PERSONALIZED VERSION: GET
###################################################################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    """
    The user sees an adaptive snippet length, question type (artist/title/year)
    chosen by analyzing which type they've missed the most, plus optional hints
    if they are struggling. Also 50% chance we pick from missed songs.
    """
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    # 1) Decide snippet length from ELO
    if user.personalized_guess_elo > 1400:
        snippet_length_seconds = 15
    elif user.personalized_guess_elo < 1100:
        snippet_length_seconds = 45
    else:
        snippet_length_seconds = 30
    session['snippet_length'] = snippet_length_seconds

    # 2) Possibly pick a missed track 50% of time
    missed_list = user.get_missed_songs()
    chosen = None
    if missed_list and random.random() < 0.5:
        missed_candidates = [t for t in ALL_TRACKS if t["id"] in missed_list]
        if missed_candidates:
            chosen = random.choice(missed_candidates)
    if not chosen:
        chosen = random.choice(ALL_TRACKS)

    # 3) Pick question type adaptively
    ctype = pick_question_type_adaptive(user)

    # 4) Store data in session so we know how to check
    session["current_track_id"] = chosen["id"]
    session["challenge_type"] = ctype
    session['question_start_time'] = time.time()

    # 5) Decide if we show hints
    # Example logic: if user’s overall accuracy < 50% AND ELO < 1300 => show hints
    show_hints = (user.get_accuracy() < 0.5) and (user.personalized_guess_elo < 1300)

    # Build hints
    hints = []
    if show_hints:
        if ctype == 'artist':
            # e.g. first letter
            hints.append(f"The artist starts with: {chosen['artist'][0]}")
        elif ctype == 'title':
            hints.append(f"The title starts with: {chosen['title'][0]}")
        elif ctype == 'year':
            # e.g. release decade
            decade = (chosen["year"] // 10) * 10
            hints.append(f"The release year is in the {decade}s")

    # 6) Buddy message logic
    lines = get_buddy_personality_lines()
    if user.get_accuracy() < 0.5:
        session["buddy_message"] = "It’s okay if you mess up—we’ll keep practicing!"
    else:
        # pick a random greeting from the 'start' lines
        session["buddy_message"] = random.choice(lines['start'])

    # 7) Render
    return render_template(
        "personalized_version.html",
        song=chosen,
        feedback=None,
        hints=hints
    )


###################################################################
# PERSONALIZED VERSION: POST (Submitting Guess)
###################################################################
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

    # Calculate how long user took
    start_time = session.pop('question_start_time', None)
    time_taken = None
    if start_time is not None:
        time_taken = time.time() - start_time

    challenge = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False

    # Extra: function to check if year guess is within margin
    def within_year_margin_adaptive(guess_year, actual_year):
        if user.personalized_guess_elo > 1400:
            # advanced user => must be exact
            return guess_year == actual_year
        else:
            # allow ±2 if user is weaker
            return abs(guess_year - actual_year) <= 2

    # Compare guess based on challenge type
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

    # Basic stats
    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    lines = get_buddy_personality_lines()

    # ELO update => approach='personalized', mode='guess'
    outcome = 1.0 if guess_correct else 0.0
    user.update_elo('personalized', 'guess', outcome)

    # If correct
    if guess_correct:
        user.total_correct += 1
        feedback = f"Correct! {track['artist']} – {track['title']} ({track['year']})."
        if time_taken is not None:
            feedback += f" You took {time_taken:.1f} seconds."

        # Remove from missed if it was there
        if track_id in missed_list:
            missed_list.remove(track_id)

        # Possibly do a “fast answer” buddy message
        if time_taken is not None and time_taken < 5:
            session["buddy_message"] = "Lightning speed! Impressive!"
        else:
            session["buddy_message"] = random.choice(lines['correct'])

    else:
        feedback = f"Wrong! Correct: {track['artist']} – {track['title']} ({track['year']})."
        if time_taken is not None:
            feedback += f" Time taken: {time_taken:.1f} seconds."

        # Add to missed if not present
        if track_id not in missed_list:
            missed_list.append(track_id)

        # Record a “miss” for that question type so we see it more in the future
        record_miss_for_type(challenge)

        session["buddy_message"] = random.choice(lines['wrong'])

    # Save updated missed list
    user.set_missed_songs(missed_list)
    db.session.commit()

    # Store feedback and redirect to a “feedback” route
    session["feedback"] = feedback
    return redirect(url_for('quiz_bp.personalized_feedback'))


###################################################################
# PERSONALIZED FEEDBACK
###################################################################
@quiz_bp.route('/personalized_feedback')
@require_login
def personalized_feedback():
    fb = session.get("feedback")
    # We pass `song=None` and `hints=[]` so the template only shows feedback
    return render_template("personalized_version.html", song=None, feedback=fb, hints=[])
