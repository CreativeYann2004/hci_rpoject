import random
import time
from flask import (
    render_template, request, redirect, url_for,
    flash, session
)
from routes.quiz_base import quiz_bp, ALL_TRACKS, RANDOM_MUSIC_FACTS
from routes.quiz_base import current_user, require_login, get_buddy_personality_lines
from app import db
from models import User

########################################
# Personalized Version (Adaptive)
########################################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select or import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    session['question_start_time'] = time.time()

    missed_list = user.get_missed_songs()
    chosen = None
    if missed_list and random.random() < 0.5:
        missed_candidates = [t for t in ALL_TRACKS if t["id"] in missed_list]
        if missed_candidates:
            chosen = random.choice(missed_candidates)

    if not chosen:
        chosen = random.choice(ALL_TRACKS)

    session["current_track_id"] = chosen["id"]
    ctype = random.choice(["artist", "title", "year"])
    session["challenge_type"] = ctype

    accuracy = user.get_accuracy()
    lines = get_buddy_personality_lines()
    if accuracy < 0.5:
        session["buddy_message"] = "It’s okay if you mess up—we’ll keep practicing!"
    else:
        session["buddy_message"] = random.choice(lines['start'])

    hints = []
    if accuracy < 0.5:
        if ctype == 'artist':
            hints.append(f"The artist starts with: {chosen['artist'][0]}")
        elif ctype == 'title':
            hints.append(f"The title starts with: {chosen['title'][0]}")
        else:
            decade = (chosen["year"] // 10) * 10
            hints.append(f"The release year is in the {decade}s")

    return render_template(
        "personalized_version.html",
        song=chosen,
        feedback=None,
        hints=hints
    )


########################################
# SUBMIT GUESS (Personalized)
########################################
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
    time_taken = None
    if start_time is not None:
        time_taken = time.time() - start_time

    challenge = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False

    def within_year_margin_adaptive(guess, actual):
        accuracy = user.get_accuracy()
        if accuracy < 0.5:
            return abs(guess - actual) <= 2
        else:
            return (guess == actual)

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

    if guess_correct:
        user.total_correct += 1
        feedback = f"Correct! The track was {track['artist']} - {track['title']} ({track['year']})."
        if time_taken is not None:
            feedback += f" You took {time_taken:.1f} seconds."

        if track_id in missed_list:
            missed_list.remove(track_id)

        if time_taken is not None and time_taken < 5:
            session["buddy_message"] = "Lightning speed! Impressive!"
        else:
            session["buddy_message"] = random.choice(lines['correct'])

    else:
        feedback = f"Wrong! The correct was {track['artist']} - {track['title']} ({track['year']})."
        if time_taken is not None:
            feedback += f" Time taken: {time_taken:.1f} seconds."

        if track_id not in missed_list:
            missed_list.append(track_id)
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
