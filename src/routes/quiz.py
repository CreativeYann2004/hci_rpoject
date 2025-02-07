import random
import os
import re
import spotipy.exceptions

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from app import db
from models import User
from routes.auth import get_spotify_client

quiz_bp = Blueprint('quiz_bp', __name__)

# Global list for all tracks from the chosen playlist
ALL_TRACKS = []

# A small list of random music facts
RANDOM_MUSIC_FACTS = [
    "The world's longest running performance is John Cage's 'Organ²/ASLSP', scheduled to end in 2640!",
    "Did you know? The Beatles were originally called The Quarrymen.",
    "Elvis Presley never performed outside of North America.",
    "The 'I' in iPod was inspired by the phrase 'Internet', not 'me/myself'.",
    "Metallica is the first and only band to have performed on all seven continents!"
]

########################################
# Helper: current_user
########################################
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)

########################################
# Require login
########################################
def require_login(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first!", "warning")
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return wrapper

########################################
# Function to parse various forms of playlist input
########################################
def parse_spotify_playlist_input(user_input):
    """
    Accepts a Spotify playlist ID, full Spotify link, or URI.
    Returns just the playlist ID.
    e.g.
      - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=...
      - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
      - 37i9dQZF1DXcBWIGoYBM5M
    """
    if not user_input:
        return ""
    user_input = user_input.strip()
    # Try matching a full URL or Spotify URI
    match = re.search(r'(?:spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)', user_input)
    if match:
        return match.group(1)
    # If it is already a plain ID, return it
    if re.match(r'^[A-Za-z0-9]+$', user_input):
        return user_input
    return user_input

########################################
# Personalized Settings
########################################
def get_personalization_settings(level):
    """
    Returns a dict with the year margin, snippet length, and whether to show hints
    based on the user's level.
    """
    if level == "beginner":
        return {
            "year_margin": 3,
            "snippet_seconds": 30,
            "show_hints": True
        }
    elif level == "intermediate":
        return {
            "year_margin": 1,
            "snippet_seconds": 15,
            "show_hints": False
        }
    else:  # advanced
        return {
            "year_margin": 0,
            "snippet_seconds": 5,
            "show_hints": False
        }

########################################
# Buddy Personality Lines
########################################
def get_buddy_personality_lines():
    """
    Returns a dict of buddy messages for 'friendly' or 'strict' personalities.
    """
    personality = session.get('buddy_personality', 'friendly')
    lines = {
        'friendly': {
            'correct': [
                "You're on fire!",
                "Woohoo! Nice job!",
                "Way to go, friend!"
            ],
            'wrong': [
                "Don't worry, you'll get it next time!",
                "Chin up—try again soon!",
            ],
            'start': [
                "Let's do this! I'm here to help!",
            ]
        },
        'strict': {
            'correct': [
                "At least you got it right this time.",
                "Alright, that was acceptable.",
            ],
            'wrong': [
                "Incorrect. Focus harder next time.",
                "You can do better than that...",
            ],
            'start': [
                "You're here again? Fine, let's get it done.",
            ]
        }
    }
    return lines.get(personality, lines['friendly'])

########################################
# Dashboard
########################################
@quiz_bp.route('/dashboard')
@require_login
def dashboard():
    user = current_user()
    # If a playlist is already set and tracks have not been loaded yet, load them.
    if session.get('playlist_id') and not ALL_TRACKS:
        sp = get_spotify_client()
        if sp:
            try:
                total, added = _fetch_playlist_tracks(sp, session['playlist_id'])
                if added == 0:
                    fallback_add_minimum_tracks()
                    flash("No valid tracks found in that playlist. Using fallback tracks...", "info")
            except spotipy.exceptions.SpotifyException as e:
                flash(f"Error auto-fetching playlist: {str(e)}", "danger")
                fallback_add_minimum_tracks()
        else:
            fallback_add_minimum_tracks()

    missed_count = 0
    if user:
        missed_count = len(user.get_missed_songs())

    if not session.get("spotify_token"):
        session["buddy_message"] = f"Hello, {user.username if user else 'Friend'}! First, connect your Spotify account above!"
    else:
        session["buddy_message"] = random.choice(get_buddy_personality_lines()['start'])

    return render_template(
        'dashboard.html',
        user=user,
        missed_count=missed_count,
        all_songs=ALL_TRACKS
    )

########################################
# Select / Import Playlist
########################################
@quiz_bp.route('/select_playlist', methods=['GET', 'POST'])
@require_login
def select_playlist():
    sp = get_spotify_client()
    if not sp:
        flash("Connect your Spotify account first!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    # Define some popular (official) playlists
    official_playlists = [
        {"name": "Today’s Top Hits", "id": "37i9dQZF1DXcBWIGoYBM5M"},
        {"name": "Global Top 50",    "id": "37i9dQZEVXbMDoHDwVN2tF"},
        {"name": "Rock Classics",    "id": "37i9dQZF1DWXRqgorJj26U"},
        {"name": "Beast Mode",       "id": "37i9dQZF1DX70RN3TfWWJh"}
    ]

    if request.method == 'GET':
        session["buddy_message"] = (
            "Pick a playlist from the options below or paste a custom link/ID. "
            "I’ll handle the details for you! :)"
        )
        try:
            user_playlists = []
            offset = 0
            while True:
                pl_data = sp.current_user_playlists(limit=50, offset=offset)
                items = pl_data.get('items', [])
                user_playlists.extend(items)
                if len(items) < 50:
                    break
                offset += 50

            return render_template(
                'select_playlist.html',
                official_playlists=official_playlists,
                user_playlists=user_playlists
            )
        except spotipy.exceptions.SpotifyException as e:
            if "expired" in str(e).lower():
                flash("Your Spotify token expired. Please reconnect.", "info")
                return redirect(url_for('auth_bp.spotify_login'))
            else:
                flash(f"Error fetching playlists: {str(e)}", "danger")
                return redirect(url_for('quiz_bp.dashboard'))

    # POST: Process submitted data
    chosen_pl = request.form.get('chosen_playlist')
    custom_pl = request.form.get('custom_playlist_id', '').strip()

    if chosen_pl:
        selected_playlist = chosen_pl
    elif custom_pl:
        selected_playlist = parse_spotify_playlist_input(custom_pl)
    else:
        flash("No playlist selected!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    session['playlist_id'] = selected_playlist

    ALL_TRACKS.clear()
    total, added = _fetch_playlist_tracks(sp, session['playlist_id'])
    if added == 0:
        fallback_add_minimum_tracks()
        flash("No valid tracks found. Using fallback tracks...", "info")
    else:
        flash(f"Imported {total} tracks; {added} loaded for guessing!", "success")

    session["buddy_message"] = (
        "Playlist imported successfully! Let's guess some songs together. I can’t wait!"
    )
    return redirect(url_for('quiz_bp.dashboard'))

########################################
# Fetch Playlist Tracks
########################################
def _fetch_playlist_tracks(sp, playlist_id, limit=300):
    """
    Fetches tracks from the selected playlist.
    """
    offset = 0
    total = 0
    added = 0
    global ALL_TRACKS

    while offset < limit:
        try:
            data = sp.playlist_items(playlist_id, limit=50, offset=offset)
        except Exception as e:
            print(f"[ERROR] offset={offset}: {e}")
            break

        items = data.get('items', [])
        if not items:
            break

        for it in items:
            track = it.get('track')
            if not track:
                continue
            if track.get('is_local') or track.get('type') != 'track':
                total += 1
                continue

            track_id = track.get('id')
            if not track_id:
                total += 1
                continue

            name = track.get('name', 'Unknown Title')
            artists = track.get('artists', [{"name": "Unknown Artist"}])
            artist_name = artists[0].get('name', '???')

            album_info = track.get('album', {})
            release_date = album_info.get('release_date', '1900-01-01')
            year = 1900
            if release_date[:4].isdigit():
                year = int(release_date[:4])

            ALL_TRACKS.append({
                "id": track_id,
                "title": name,
                "artist": artist_name,
                "year": year
            })
            added += 1
            total += 1

        offset += 50
        if offset >= data.get('total', 0):
            break

    return total, added

########################################
# Fallback Tracks
########################################
def fallback_add_minimum_tracks():
    global ALL_TRACKS
    fallback_songs = [
        {
            "id": "4uLU6hMCjMI75M1A2tKUQC",  # Rick Astley
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
            "year": 1987
        },
        {
            "id": "3n3Ppam7vgaVa1iaRUc9Lp",  # The Killers
            "title": "Mr. Brightside",
            "artist": "The Killers",
            "year": 2003
        }
    ]
    if not ALL_TRACKS:
        ALL_TRACKS.extend(fallback_songs)

########################################
# SETTINGS
########################################
@quiz_bp.route('/settings', methods=['GET', 'POST'])
@require_login
def settings():
    """
    Allows the user to update visual settings and set buddy personality.
    """
    user = current_user()
    if request.method == 'POST':
        session['color_theme'] = request.form.get('color_theme', 'navy')
        session['difficulty'] = request.form.get('difficulty', 'normal')
        custom_pl = request.form.get('playlist_id', '').strip()
        if custom_pl:
            parsed_pl = parse_spotify_playlist_input(custom_pl)
            session['playlist_id'] = parsed_pl

        chosen_personality = request.form.get('buddy_personality', 'friendly')
        session['buddy_personality'] = chosen_personality

        flash("Settings updated!", "success")
        return redirect(url_for('quiz_bp.dashboard'))

    color = session.get('color_theme', 'navy')
    diff = session.get('difficulty', 'normal')
    curr_pl = session.get('playlist_id', '')
    curr_buddy = session.get('buddy_personality', 'friendly')

    session["buddy_message"] = "Customize your theme, difficulty, and buddy personality here!"
    return render_template(
        'settings.html',
        color_theme=color,
        current_diff=diff,
        current_playlist=curr_pl,
        buddy_personality=curr_buddy
    )

########################################
# RANDOM UI
########################################
@quiz_bp.route('/random_version')
@require_login
def random_version():
    if not ALL_TRACKS:
        fallback_add_minimum_tracks()
    if not ALL_TRACKS:
        flash("No tracks found and fallback is empty!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    chosen = random.choice(ALL_TRACKS)
    session["current_track_id"] = chosen["id"]
    ctype = random.choice(["artist", "title", "year"])
    session["challenge_type"] = ctype

    lines = get_buddy_personality_lines()
    start_line = random.choice(lines['start'])
    session["buddy_message"] = f"Random mode: {start_line}"
    return render_template('random_version.html', song=chosen, feedback=None)

########################################
# PERSONALIZED UI
########################################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    user = current_user()
    if not ALL_TRACKS:
        fallback_add_minimum_tracks()
    if not ALL_TRACKS:
        flash("No tracks found and fallback is empty!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    level = user.get_level()  # "beginner", "intermediate", "advanced"
    settings_dict = get_personalization_settings(level)
    session["personal_year_margin"] = settings_dict["year_margin"]
    session["personal_snippet_secs"] = settings_dict["snippet_seconds"]
    session["personal_show_hints"] = settings_dict["show_hints"]

    missed_list = user.get_missed_songs()
    chosen = None

    accuracy = user.get_accuracy()
    re_show_prob = 0.8 if level == "beginner" or accuracy < 0.5 else 0.5

    if missed_list and random.random() < re_show_prob:
        missed_candidates = [t for t in ALL_TRACKS if t["id"] in missed_list]
        if missed_candidates:
            chosen = random.choice(missed_candidates)

    if not chosen:
        chosen = random.choice(ALL_TRACKS)

    session["current_track_id"] = chosen["id"]
    ctype = random.choice(["artist", "title", "year"])
    session["challenge_type"] = ctype

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['start']) + " Personalized mode: let's see how you do!"
    return render_template(
        'personalized_version.html',
        song=chosen,
        missed_count=len(missed_list),
        feedback=None,
        snippet_secs=settings_dict["snippet_seconds"],
        show_hints=settings_dict["show_hints"]
    )

########################################
# SUBMIT GUESS
########################################
@quiz_bp.route('/submit_guess', methods=['POST'])
@require_login
def submit_guess():
    user = current_user()
    track_id = session.get("current_track_id")
    if not track_id:
        flash("No active track. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    track = next((t for t in ALL_TRACKS if t["id"] == track_id), None)
    if not track:
        flash("Track not found. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    challenge = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False

    personal_margin = session.get("personal_year_margin")

    def within_year_margin_dynamic(guess, actual):
        if personal_margin is not None:
            return abs(guess - actual) <= personal_margin
        else:
            diff = session.get('difficulty', 'normal')
            if diff == 'easy':
                return abs(guess - actual) <= 3
            elif diff == 'normal':
                return abs(guess - actual) <= 1
            else:
                return guess == actual

    if challenge == "artist":
        if guess_str == track["artist"].lower():
            guess_correct = True
    elif challenge == "title":
        if guess_str == track["title"].lower():
            guess_correct = True
    elif challenge == "year":
        if guess_str.isdigit():
            guess_year = int(guess_str)
            if within_year_margin_dynamic(guess_year, track["year"]):
                guess_correct = True

    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    lines = get_buddy_personality_lines()

    if guess_correct:
        user.total_correct += 1
        feedback = f"Correct! The track was: {track['artist']} - {track['title']} ({track['year']})"
        if track_id in missed_list:
            missed_list.remove(track_id)

        src = request.form.get("source_page", "random")
        if src == "random":
            fact = random.choice(RANDOM_MUSIC_FACTS)
            session["buddy_message"] = fact
        else:
            session["buddy_message"] = random.choice(lines['correct'])
    else:
        feedback = f"Wrong! The correct was: {track['artist']} - {track['title']} ({track['year']})"
        if track_id not in missed_list:
            missed_list.append(track_id)
        session["buddy_message"] = random.choice(lines['wrong'])

    user.set_missed_songs(missed_list)
    db.session.commit()
    session["feedback"] = feedback

    if request.form.get("source_page") == "random":
        return redirect(url_for('quiz_bp.random_feedback'))
    else:
        return redirect(url_for('quiz_bp.personalized_feedback'))

########################################
# FEEDBACK ROUTES
########################################
@quiz_bp.route('/random_feedback')
@require_login
def random_feedback():
    fb = session.get("feedback")
    return render_template('random_version.html', song=None, feedback=fb)

@quiz_bp.route('/personalized_feedback')
@require_login
def personalized_feedback():
    fb = session.get("feedback")
    return render_template('personalized_version.html', song=None, missed_count=0, feedback=fb)

########################################
# SCOREBOARD
########################################
@quiz_bp.route('/scoreboard')
@require_login
def scoreboard():
    users = User.query.all()

    def accuracy(u):
        if u.total_attempts == 0:
            return 0
        return u.total_correct / u.total_attempts

    sorted_users = sorted(users, key=lambda x: (accuracy(x), x.total_correct), reverse=True)
    return render_template('scoreboard.html', all_users=sorted_users)

########################################
# AUTOCOMPLETE for Artist
########################################
@quiz_bp.route('/autocomplete/tab_artist')
@require_login
def tab_autocomplete_artist():
    """
    In random mode, we use standard logic.
    In personalized mode, advanced users get minimal help,
    while beginners receive partial suggestions.
    """
    query = request.args.get("query", "").strip().lower()
    if not query:
        return jsonify({"match": ""})

    user = current_user()
    if not user:
        return jsonify({"match": ""})

    level = user.get_level()  # "beginner", "intermediate", "advanced"
    artist_names = {t["artist"].lower() for t in ALL_TRACKS}
    
    if level == "advanced":
        for art in artist_names:
            if art == query:
                return jsonify({"match": art})
        return jsonify({"match": ""})
    elif level == "beginner":
        for art in artist_names:
            if query in art:
                return jsonify({"match": art})
        return jsonify({"match": ""})
    else:
        for art in artist_names:
            if art.startswith(query):
                return jsonify({"match": art})
        return jsonify({"match": ""})
