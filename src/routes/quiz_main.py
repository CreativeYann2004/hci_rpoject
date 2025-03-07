import os
import re
import random
import time
from functools import wraps

import spotipy
import spotipy.exceptions
from flask import (
    Blueprint,
    session,
    redirect,
    url_for,
    flash,
    render_template,
    request,
    jsonify,
    send_file,
    abort  # <-- needed for the download route
)
from src.app import db
from src.models import User, GuessLog
from src.routes.auth import get_spotify_client

quiz_bp = Blueprint("quiz_bp", __name__)

########################################
# SHARED GLOBALS
########################################
ALL_TRACKS = []

RANDOM_MUSIC_FACTS = [
    "The world's longest running performance is John Cage's 'Organ²/ASLSP', scheduled to end in 2640!",
    "Did you know? The Beatles were originally called The Quarrymen.",
    "Elvis Presley never performed outside of North America.",
    "The 'I' in iPod was inspired by the phrase 'Internet', not 'me/myself'.",
    "Metallica is the first and only band to have performed on all seven continents!"
]

########################################
# SHARED HELPERS
########################################
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)

def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first!", "warning")
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return wrapper

def parse_spotify_playlist_input(user_input):
    if not user_input:
        return ""
    user_input = user_input.strip()
    # Look for either Spotify playlist link or URN
    match = re.search(r'(?:spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)', user_input)
    if match:
        return match.group(1)
    if re.match(r'^[A-Za-z0-9]+$', user_input):
        return user_input
    return user_input

def get_buddy_personality_lines():
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
            ],
            'rank': [
                "Ranking time! Let's see what you think!",
                "Time to order some tracks—have fun!"
            ],
            'hint': [
                "Psst—need a clue?",
                "A little hint won't hurt, right?",
                "Okay, here's a nudge in the right direction…"
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
            ],
            'rank': [
                "Ranking again? Don’t mess it up.",
                "Focus and get it right this time."
            ],
            'hint': [
                "Try harder. Here's a tiny clue.",
                "I'm only giving this hint once.",
                "You shouldn't need a hint, but here you go..."
            ]
        }
    }
    return lines.get(personality, lines['friendly'])

########################################
# HELPER: fetch playlist tracks
########################################
def _fetch_playlist_tracks(sp, playlist_id, limit=300):
    offset = 0
    total = 0
    added = 0

    global ALL_TRACKS
    while offset < limit:
        data = sp.playlist_items(
            playlist_id,
            limit=50,
            offset=offset,
            market="from_token"
        )
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
            preview_url = track.get('preview_url')
            album_info = track.get('album', {})
            release_date = album_info.get('release_date', '1900-01-01')
            year = 1900
            if release_date[:4].isdigit():
                year = int(release_date[:4])

            popularity = track.get('popularity', 0)

            ALL_TRACKS.append({
                "id": track_id,
                "title": name,
                "artist": artist_name,
                "year": year,
                "preview_url": preview_url,
                "popularity": popularity
            })
            added += 1
            total += 1

        offset += 50
        if offset >= data.get('total', 0):
            break

    return (total, added)

########################################
# HINT GENERATION
########################################
def generate_hints(track, question_type, user, is_personalized=False):
    """
    Return a list of hint strings to display. 
    The buddy will re-say them until the user guesses.
    """
    hints = []

    user_elo = user.personalized_guess_elo if is_personalized else user.random_guess_elo

    # Example logic: advanced => fewer hints
    max_hints = 3
    if user_elo > 1400:
        max_hints = 1
    elif user_elo < 1100:
        max_hints = 3  # allow full hints

    if question_type == 'artist':
        first_letter = track['artist'][0]
        hints.append(f"The artist starts with '{first_letter}'.")
        if is_personalized and max_hints > 1:
            name_len = len(track['artist'])
            hints.append(f"The artist's name has {name_len} letters.")

    elif question_type == 'title':
        first_letter = track['title'][0]
        hints.append(f"The title starts with '{first_letter}'.")
        if is_personalized and max_hints > 1:
            hints.append(f"The title has {len(track['title'])} characters.")

    elif question_type == 'year':
        decade = (track['year'] // 10) * 10
        hints.append(f"The release year is in the {decade}s.")
        if is_personalized and max_hints > 1:
            if track['year'] >= 2000:
                hints.append("It's in the 21st century.")
            else:
                hints.append("It's in the 20th century or earlier.")

    times_missed = user.get_missed_songs().count(track["id"])
    if times_missed >= 2 and is_personalized and max_hints > 1:
        hints.append("Remember, we've seen this track a few times already. Focus!")

    return hints[:max_hints]

########################################
# QUIZ DASHBOARD & SETTINGS
########################################
@quiz_bp.route('/dashboard')
@require_login
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for('auth_bp.login'))

    missed_count = len(user.get_missed_songs())

    # If we have a playlist_id but ALL_TRACKS is empty, auto-fetch now:
    if session.get('playlist_id') and not ALL_TRACKS:
        sp = get_spotify_client()
        if sp:
            try:
                total, added = _fetch_playlist_tracks(sp, session['playlist_id'])
                flash(f"Imported {added} track(s) from your playlist.", "info")
            except spotipy.exceptions.SpotifyException as e:
                flash(f"Error auto-fetching playlist: {str(e)}", "danger")

    if not session.get("spotify_token"):
        session["buddy_message"] = f"Hello, {user.username}! Connect your Spotify account above!"
    else:
        lines = get_buddy_personality_lines()
        session["buddy_message"] = lines['start'][0]

    return render_template('dashboard.html',
                           user=user,
                           missed_count=missed_count,
                           all_songs=ALL_TRACKS)

@quiz_bp.route('/settings', methods=['GET', 'POST'])
@require_login
def settings():
    user = current_user()
    if request.method == 'POST':
        session['color_theme'] = request.form.get('color_theme', 'navy')
        session['difficulty'] = request.form.get('difficulty', 'normal')
        custom_pl = request.form.get('playlist_id', '').strip()
        if custom_pl:
            parsed_pl = parse_spotify_playlist_input(custom_pl)
            session['playlist_id'] = parsed_pl
            ALL_TRACKS.clear()
        chosen_personality = request.form.get('buddy_personality', 'friendly')
        session['buddy_personality'] = chosen_personality

        flash("Settings updated!", "success")
        return redirect(url_for('quiz_bp.dashboard'))

    color = session.get('color_theme', 'navy')
    diff = session.get('difficulty', 'normal')
    curr_pl = session.get('playlist_id', '')
    curr_buddy = session.get('buddy_personality', 'friendly')

    return render_template(
        'settings.html',
        color_theme=color,
        current_diff=diff,
        current_playlist=curr_pl,
        buddy_personality=curr_buddy
    )

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
# SELECT PLAYLIST
########################################
@quiz_bp.route('/select_playlist', methods=['GET','POST'])
@require_login
def select_playlist():
    sp = get_spotify_client()
    if not sp:
        flash("Connect your Spotify account first!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    official_playlists = [
        {"name": "Today’s Top Hits", "id": "37i9dQZF1DXcBWIGoYBM5M"},
        {"name": "Global Top 50",    "id": "37i9dQZEVXbMDoHDwVN2tF"},
        {"name": "Rock Classics",    "id": "37i9dQZF1DWXRqgorJj26U"},
        {"name": "Beast Mode",       "id": "37i9dQZF1DX70RN3TfWWJh"}
    ]

    if request.method == 'GET':
        user_playlists = []
        offset = 0
        try:
            while True:
                pl_data = sp.current_user_playlists(limit=50, offset=offset)
                items = pl_data.get('items', [])
                user_playlists.extend(items)
                if len(items) < 50:
                    break
                offset += 50
        except spotipy.exceptions.SpotifyException as e:
            flash(f"Error fetching your playlists: {e}", "danger")
            return redirect(url_for('quiz_bp.dashboard'))

        return render_template(
            "select_playlist.html",
            official_playlists=official_playlists,
            user_playlists=user_playlists
        )

    chosen_pl = request.form.get('chosen_playlist')
    custom_pl = request.form.get('custom_playlist_id','').strip()
    if chosen_pl:
        selected_playlist = chosen_pl
    elif custom_pl:
        selected_playlist = parse_spotify_playlist_input(custom_pl)
    else:
        flash("No playlist selected!", "warning")
        return redirect(url_for('quiz_bp.select_playlist'))

    session['playlist_id'] = selected_playlist
    ALL_TRACKS.clear()
    flash("Playlist selected! Tracks will load once you start playing or return to the dashboard.", "success")
    return redirect(url_for('quiz_bp.dashboard'))

@quiz_bp.route('/recent')
@require_login
def recent_played():
    sp = get_spotify_client()
    if not sp:
        flash("Connect your Spotify account first!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    try:
        results = sp.current_user_recently_played(limit=20)
    except spotipy.exceptions.SpotifyException as e:
        flash(f"Error fetching recent tracks: {str(e)}", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    tracks = []
    for item in results.get('items', []):
        t = item.get('track')
        if t:
            tname = t.get('name', 'Unknown')
            artists = t.get('artists', [])
            aname = artists[0]['name'] if artists else 'Unknown'
            played_at = item.get('played_at','')
            tracks.append({
                'title': tname,
                'artist': aname,
                'played_at': played_at
            })
    return render_template("recent.html", tracks=tracks)

########################################
# RANDOM GUESS MODE
########################################
@quiz_bp.route('/random_version')
@require_login
def random_version():
    sp = get_spotify_client()
    if not sp:
        flash("Spotify not connected. Please connect first!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    if not ALL_TRACKS:
        if not session.get('playlist_id'):
            flash("No playlist selected. Please import a playlist first.", "warning")
            return redirect(url_for('quiz_bp.dashboard'))
        try:
            total, added = _fetch_playlist_tracks(sp, session['playlist_id'])
            if not added:
                flash("No new tracks found (maybe the playlist is empty?).", "warning")
        except spotipy.exceptions.SpotifyException as e:
            flash(f"Error fetching playlist: {str(e)}", "danger")
            return redirect(url_for('quiz_bp.dashboard'))

    if not ALL_TRACKS:
        flash("No tracks found. Please select or import a valid playlist!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    chosen = random.choice(ALL_TRACKS)
    session["current_track_id"] = chosen["id"]
    ctype = random.choice(["artist", "title", "year"])
    session["challenge_type"] = ctype

    lines = get_buddy_personality_lines()

    # 40% chance to show a hint
    show_hint = (random.random() < 0.4)
    if show_hint:
        from src.routes.quiz_base import generate_hints
        generated = generate_hints(chosen, ctype, current_user(), is_personalized=False)
        if generated:
            final_hint_text = " | ".join(generated)
            session['buddy_hint'] = final_hint_text
            session["buddy_message"] = random.choice(lines['hint'])
        else:
            session.pop('buddy_hint', None)
            session["buddy_message"] = random.choice(lines['start'])
    else:
        session.pop('buddy_hint', None)
        session["buddy_message"] = random.choice(lines['start'])

    return render_template('random_version.html', song=chosen, feedback=None, hints=[])

@quiz_bp.route('/submit_guess', methods=['POST'])
@require_login
def submit_guess():
    user = current_user()
    track_id = session.get("current_track_id")
    if not track_id:
        flash("No active track. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    chosen = next((t for t in ALL_TRACKS if t["id"] == track_id), None)
    if not chosen:
        flash("Track not found. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    challenge = session.get("challenge_type", "artist")
    guess_str = request.form.get("guess", "").strip().lower()
    guess_correct = False
    time_taken = 0.0

    def within_year_margin_dynamic(g, actual):
        diff = session.get('difficulty', 'normal')
        if diff == 'easy':
            return abs(g - actual) <= 3
        elif diff == 'normal':
            return abs(g - actual) <= 1
        else:
            return g == actual

    if challenge == "artist":
        if guess_str == chosen["artist"].lower():
            guess_correct = True
    elif challenge == "title":
        if guess_str == chosen["title"].lower():
            guess_correct = True
    elif challenge == "year":
        if guess_str.isdigit():
            guess_year = int(guess_str)
            if within_year_margin_dynamic(guess_year, chosen["year"]):
                guess_correct = True

    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    from src.routes.quiz_base import get_buddy_personality_lines
    lines = get_buddy_personality_lines()

    outcome = 1.0 if guess_correct else 0.0
    user.update_elo('random', 'guess', outcome)

    # Log attempt
    guess_log = GuessLog(
        user_id=user.id,
        track_id=track_id,
        question_type=challenge,
        is_correct=guess_correct,
        time_taken=time_taken,
        approach='random'
    )
    db.session.add(guess_log)

    if guess_correct:
        user.total_correct += 1
        feedback = f"Correct! {chosen['artist']} - {chosen['title']} ({chosen['year']})"
        if chosen["id"] in missed_list:
            missed_list.remove(chosen["id"])
        fact = random.choice(RANDOM_MUSIC_FACTS)
        session["buddy_message"] = fact
        session.pop('buddy_hint', None)
    else:
        feedback = f"Wrong! Correct: {chosen['artist']} - {chosen['title']} ({chosen['year']})"
        if chosen["id"] not in missed_list:
            missed_list.append(chosen["id"])
        session["buddy_message"] = random.choice(lines['wrong'])

    user.set_missed_songs(missed_list)
    db.session.commit()

    session["feedback"] = feedback
    return redirect(url_for('quiz_bp.random_feedback'))

@quiz_bp.route('/random_feedback')
@require_login
def random_feedback():
    fb = session.get("feedback")
    return render_template('random_version.html', song=None, feedback=fb, hints=[])

@quiz_bp.route('/choose_playlist', methods=['GET', 'POST'])
@require_login
def choose_playlist():
    sp = get_spotify_client()
    if not sp:
        flash("Please connect your Spotify account first!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    if request.method == 'POST':
        choice_source = request.form.get('choice_source')
        if choice_source == 'user':
            selected_id = request.form.get('playlist_id')
            if selected_id:
                session['playlist_id'] = selected_id
                ALL_TRACKS.clear()
                flash("Selected your playlist!", "success")
            else:
                flash("No playlist chosen.", "warning")

        elif choice_source == 'custom':
            custom_val = request.form.get('custom_playlist_link', '').strip()
            if custom_val:
                parsed_id = parse_spotify_playlist_input(custom_val)
                if parsed_id:
                    session['playlist_id'] = parsed_id
                    ALL_TRACKS.clear()
                    flash("Custom playlist set!", "success")
                else:
                    flash("Unable to parse that link or ID!", "danger")
            else:
                flash("No custom link provided.", "warning")

        return redirect(url_for('quiz_bp.dashboard'))

    user_playlists = []
    try:
        sp = get_spotify_client()
        if sp:
            result = sp.current_user_playlists(limit=50, offset=0)
            user_playlists = result.get('items', [])
    except Exception as e:
        flash(f"Error fetching your playlists: {str(e)}", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    return render_template(
        "choose_playlist.html",
        user_playlists=user_playlists
    )

# Autocomplete for artists
@quiz_bp.route('/autocomplete/artist')
@require_login
def autocomplete_artist():
    q = request.args.get("q", "").lower()
    if not q:
        return jsonify([])
    artists = set()
    for track in ALL_TRACKS:
        if track["artist"].lower().startswith(q):
            artists.add(track["artist"])
    suggestions = sorted(list(artists), key=lambda x: x.lower())
    return jsonify(suggestions[:10])

###############################################################################
# NEW: Autocomplete for Titles
###############################################################################
@quiz_bp.route('/autocomplete/title')
@require_login
def autocomplete_title():
    q = request.args.get("q", "").lower()
    if not q:
        return jsonify([])
    titles = set()
    for track in ALL_TRACKS:
        if track["title"].lower().startswith(q):
            titles.add(track["title"])
    suggestions = sorted(list(titles), key=lambda x: x.lower())
    return jsonify(suggestions[:10])

###############################################################################
# DOWNLOAD ROUTE (PASSWORD-PROTECTED)
###############################################################################
SECRET_DOWNLOAD_PASSWORD = "music"

@quiz_bp.route("/admin/download_db")
def download_db():
    # Check a query param or basic auth
    passwd = request.args.get("pw")
    if passwd != SECRET_DOWNLOAD_PASSWORD:
        abort(403)

    # Adjust if your DB file is in a different location
    db_path = os.path.join(os.path.dirname(__file__), "src\instance\guessing_game.db")
    # If the DB is next to 'app.py', you might do something like:
    #   db_path = os.path.join(current_app.root_path, "guessing_game.db")
    # or similar, depending on your structure.

    return send_file(
        db_path,
        as_attachment=True,
        attachment_filename="guessing_game.db"
    )


