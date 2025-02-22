# routes/quiz_personalized.py
import random
import time
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

def get_user_type_stats(user):
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    return session['type_misses']

def record_miss_for_type(question_type):
    if 'type_misses' not in session:
        session['type_misses'] = {'artist': 0, 'title': 0, 'year': 0}
    session['type_misses'][question_type] += 1

def pick_question_type_adaptive(user):
    type_stats = get_user_type_stats(user)
    total_misses = sum(type_stats.values())
    if total_misses == 0:
        return random.choice(["artist", "title", "year"])

    weighted_pool = []
    for qtype in ["artist", "title", "year"]:
        count = type_stats.get(qtype, 0)
        weighted_pool += [qtype] * (count + 1)

    return random.choice(weighted_pool)

@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    user = current_user()
    if not ALL_TRACKS:
        flash("No tracks found. Please select/import a playlist first.", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    # snippet length from ELO
    if user.personalized_guess_elo > 1400:
        snippet_length_seconds = 15
    elif user.personalized_guess_elo < 1100:
        snippet_length_seconds = 45
    else:
        snippet_length_seconds = 30
    session['snippet_length'] = snippet_length_seconds

    missed_list = user.get_missed_songs()
    chosen = None
    if missed_list and random.random() < 0.5:
        missed_candidates = [t for t in ALL_TRACKS if t["id"] in missed_list]
        if missed_candidates:
            chosen = random.choice(missed_candidates)
    if not chosen:
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

    # NEW: Log attempt
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
        # Keep session['buddy_hint'] if we want them to guess again
        # or you can pop it if you prefer to regenerate hints next time

    user.set_missed_songs(missed_list)
    db.session.commit()
    session["feedback"] = feedback
    return redirect(url_for('quiz_bp.personalized_feedback'))


@quiz_bp.route('/personalized_feedback')
@require_login
def personalized_feedback():
    fb = session.get("feedback")
    return render_template("personalized_version.html", song=None, feedback=fb, hints=[])
