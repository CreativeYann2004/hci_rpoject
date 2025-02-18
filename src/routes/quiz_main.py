import random
import spotipy.exceptions
from flask import (
    render_template, request, redirect, url_for,
    session, flash
)
from app import db
from models import User
from routes.auth import get_spotify_client
from routes.quiz_base import quiz_bp, ALL_TRACKS, RANDOM_MUSIC_FACTS
from routes.quiz_base import current_user, require_login
from routes.quiz_base import parse_spotify_playlist_input, get_buddy_personality_lines

########################################
# DASHBOARD
########################################
@quiz_bp.route('/dashboard')
@require_login
def dashboard():
    user = current_user()

    # If we have a playlist in session, but haven't fetched tracks, do so:
    if session.get('playlist_id') and not ALL_TRACKS:
        sp = get_spotify_client()
        if sp:
            try:
                total, added = _fetch_playlist_tracks(sp, session['playlist_id'])
                flash(f"Imported {added} tracks from that playlist.", "info")
            except spotipy.exceptions.SpotifyException as e:
                flash(f"Error auto-fetching playlist: {str(e)}", "danger")

    missed_count = 0
    if user:
        missed_count = len(user.get_missed_songs())

    # Buddy greeting
    if not session.get("spotify_token"):
        session["buddy_message"] = (
            f"Hello, {user.username if user else 'Friend'}! Connect your Spotify account above!"
        )
    else:
        lines = get_buddy_personality_lines()
        session["buddy_message"] = lines['start'][0]

    return render_template(
        'dashboard.html',
        user=user,
        missed_count=missed_count,
        all_songs=ALL_TRACKS
    )


########################################
# CHOOSE QUIZ: Single Button => Choose Random or Personalized
########################################
@quiz_bp.route('/choose_quiz', methods=['GET', 'POST'])
@require_login
def choose_quiz():
    """
    A simple route that asks user which type of quiz to play.
    Replaces separate random/personalized buttons with a single selection step.
    """
    if request.method == 'POST':
        choice = request.form.get('quiz_choice')
        if choice == 'random':
            return redirect(url_for('quiz_bp.random_version'))
        else:
            return redirect(url_for('quiz_bp.personalized_version'))

    # GET => show a simple template with two radio buttons or something
    return render_template("choose_quiz.html")


########################################
# SELECT / IMPORT PLAYLIST
########################################
@quiz_bp.route('/select_playlist', methods=['GET', 'POST'])
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
        session["buddy_message"] = "Pick a playlist or paste a custom link."
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

    # POST branch: user submitted a chosen or custom playlist
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
    ALL_TRACKS.clear()  # Clear old tracks from memory, let /dashboard auto-fetch them
    flash("Playlist selected! Tracks will load on the dashboard.", "success")
    return redirect(url_for('quiz_bp.dashboard'))


########################################
# FETCH PLAYLIST TRACKS
########################################
def _fetch_playlist_tracks(sp, playlist_id, limit=300):
    offset = 0
    total = 0
    added = 0
    global ALL_TRACKS

    while offset < limit:
        data = sp.playlist_items(playlist_id, limit=50, offset=offset, market="from_token")
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

            ALL_TRACKS.append({
                "id": track_id,
                "title": name,
                "artist": artist_name,
                "year": year,
                "preview_url": preview_url
            })
            added += 1
            total += 1

        offset += 50
        if offset >= data.get('total', 0):
            break

    return total, added


########################################
# SETTINGS
########################################
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
# RANDOM UI VERSION
########################################
@quiz_bp.route('/random_version')
@require_login
def random_version():
    if not ALL_TRACKS:
        flash("No tracks found in memory. Please select or import a playlist first.", "warning")
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
# SUBMIT GUESS (used by Random UI)
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

    def within_year_margin_dynamic(guess, actual):
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
        return redirect(url_for('quiz_bp.dashboard'))


########################################
# RANDOM FEEDBACK
########################################
@quiz_bp.route('/random_feedback')
@require_login
def random_feedback():
    fb = session.get("feedback")
    return render_template('random_version.html', song=None, feedback=fb)


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
# RECENT
########################################
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
        track = item.get('track')
        if track:
            tname = track.get('name', 'Unknown')
            artists = track.get('artists', [])
            aname = artists[0]['name'] if artists else 'Unknown'
            played_at = item.get('played_at', '')
            tracks.append({
                'title': tname,
                'artist': aname,
                'played_at': played_at
            })
    return render_template('recent.html', tracks=tracks)


# =================================================================
# BUDDY RANDOM PLAYLIST GENERATOR (Tinder-like)
# =================================================================

@quiz_bp.route('/buddy_generator', methods=['GET', 'POST'])
@require_login
def buddy_generator():
    from routes.auth import get_spotify_client
    sp = get_spotify_client()
    if not sp:
        flash("Spotify not connected. Please connect first!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))

    lines = get_buddy_personality_lines()
    session["buddy_message"] = random.choice(lines['start']) + " Let's build a random playlist for you!"

    # A small set of "allowed" genres so we can fallback if user enters nonsense
    allowed_genres = {
        "pop", "rock", "dance", "hip-hop", "jazz", "metal", "country", "r&b", 
        "edm", "classical", "reggae", "blues", "indie", "latin"
    }

    if request.method == 'POST':
        raw_genre = request.form.get("genre", "pop").strip().lower()
        if raw_genre not in allowed_genres:
            # fallback to "pop"
            genre = "pop"
            flash("Unknown genre. Falling back to 'pop'.", "info")
        else:
            genre = raw_genre

        try:
            num_tracks = int(request.form.get("num_tracks", 5))
        except:
            num_tracks = 5
        if num_tracks < 1:
            num_tracks = 5
        if num_tracks > 20:
            num_tracks = 20

        try:
            # fetch recs from Spotify using the single genre as a seed, ignoring personal data
            recs = sp.recommendations(seed_genres=[genre], limit=num_tracks)
            tracks = recs.get('tracks', [])
            buddy_tracks = []
            for t in tracks:
                tid = t.get('id')
                name = t.get('name', 'Unknown')
                artist_objs = t.get('artists', [{}])
                artist = artist_objs[0].get('name', 'Unknown')
                preview_url = t.get('preview_url')
                album = t.get('album', {})
                release_date = album.get('release_date', '1900')
                year = 1900
                if release_date[:4].isdigit():
                    year = int(release_date[:4])

                buddy_tracks.append({
                    "id": tid,
                    "title": name,
                    "artist": artist,
                    "year": year,
                    "preview_url": preview_url
                })

            # Save them in session
            session["buddy_tracks"] = buddy_tracks
            session["buddy_kept"] = []  # empty initially
            flash(f"Fetched {len(buddy_tracks)} tracks for genre '{genre}'.", "info")
            return redirect(url_for('quiz_bp.buddy_tinder'))

        except spotipy.exceptions.SpotifyException as e:
            flash(f"Error fetching recommendations: {str(e)}", "danger")
            return redirect(url_for('quiz_bp.dashboard'))

    # GET => show the form
    return render_template("buddy_generator.html")


@quiz_bp.route('/buddy_tinder', methods=['GET'])
@require_login
def buddy_tinder():
    buddy_tracks = session.get("buddy_tracks", [])
    if not buddy_tracks:
        flash("No more tracks to rate; let's finalize!", "info")
        return redirect(url_for('quiz_bp.buddy_finalize'))

    current = buddy_tracks[0]
    return render_template("buddy_tinder.html", track=current)


@quiz_bp.route('/buddy_tinder_choice/<action>', methods=['POST'])
@require_login
def buddy_tinder_choice(action):
    buddy_tracks = session.get("buddy_tracks", [])
    buddy_kept = session.get("buddy_kept", [])

    if not buddy_tracks:
        return redirect(url_for('quiz_bp.buddy_finalize'))

    current = buddy_tracks.pop(0)
    if action == "keep":
        buddy_kept.append(current)

    session["buddy_tracks"] = buddy_tracks
    session["buddy_kept"] = buddy_kept

    if not buddy_tracks:
        return redirect(url_for('quiz_bp.buddy_finalize'))
    else:
        return redirect(url_for('quiz_bp.buddy_tinder'))


@quiz_bp.route('/buddy_finalize', methods=['GET', 'POST'])
@require_login
def buddy_finalize():
    sp = get_spotify_client()
    if not sp:
        flash("Spotify not connected!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    buddy_kept = session.get("buddy_kept", [])
    if request.method == 'POST':
        if "discard" in request.form:
            session["buddy_tracks"] = []
            session["buddy_kept"] = []
            flash("Discarded the generated playlist!", "info")
            return redirect(url_for('quiz_bp.dashboard'))

        # user wants to save
        playlist_name = request.form.get("playlist_name", "My Buddy Playlist")
        playlist_desc = request.form.get("playlist_desc", "Created by Buddy Generator")
        user_id = sp.current_user()["id"]

        try:
            new_pl = sp.user_playlist_create(
                user=user_id,
                name=playlist_name,
                public=False,
                description=playlist_desc
            )
            track_ids = [f"spotify:track:{t['id']}" for t in buddy_kept if t['id']]
            if track_ids:
                sp.playlist_add_items(new_pl["id"], track_ids)

            flash(f"Playlist '{playlist_name}' created with {len(track_ids)} track(s).", "success")
        except spotipy.exceptions.SpotifyException as e:
            flash(f"Error creating playlist on Spotify: {str(e)}", "danger")

        session["buddy_tracks"] = []
        session["buddy_kept"] = []
        return redirect(url_for('quiz_bp.dashboard'))

    return render_template("buddy_finalize.html", kept=buddy_kept)
