from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app import db
from models import User
import random
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from routes.auth import get_spotify_client, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
import spotipy.exceptions

quiz_bp = Blueprint('quiz_bp', __name__)

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)

def require_login(f):
    from functools import wraps
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first!", "warning")
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return wraps(f)(wrapper)

# Global track store
ALL_TRACKS = []

########################################
# Difficulty Logic
########################################
def get_difficulty():
    return session.get('difficulty', 'normal')

def within_year_margin(guess_year, actual_year):
    diff = get_difficulty()
    if diff == 'easy':
        return abs(guess_year - actual_year) <= 3
    elif diff == 'normal':
        return abs(guess_year - actual_year) <= 1
    else:
        return guess_year == actual_year

########################################
# Spotify client credentials fallback
########################################
def get_spotify_client_credentials():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    auth_manager = SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
    return spotipy.Spotify(auth_manager=auth_manager)

########################################
# (Helper) Try multiple fallback markets for preview
########################################
def _try_get_preview_for_track(sp, track_id, track_name):
    possible_markets = ["", "US", "GB", "DE", "ES", "FR"]
    for mk in possible_markets:
        try:
            full_track = sp.track(track_id, market=mk or None)
            if full_track and full_track.get('preview_url'):
                return full_track['preview_url']
        except Exception as e:
            print(f"[DEBUG] track({track_id}, mk={mk}) => ERROR: {str(e)}")
    return None

########################################
# NEW: Predefined / "Official" Playlists
########################################
@quiz_bp.route('/predefined_playlists', methods=['GET','POST'])
@require_login
def predefined_playlists():
    official_playlists = [
        {"name": "Today’s Top Hits", "id": "37i9dQZF1DXcBWIGoYBM5M"},
        {"name": "Global Top 50",    "id": "37i9dQZEVXbMDoHDwVN2tF"},
        {"name": "Rock Classics",    "id": "37i9dQZF1DWXRqgorJj26U"},
        {"name": "Beast Mode",       "id": "37i9dQZF1DX70RN3TfWWJh"},
    ]
    if request.method == 'GET':
        return render_template('pick_predefined.html', official_playlists=official_playlists)
    chosen_pl = request.form.get('playlist_id')
    if not chosen_pl:
        flash("No predefined playlist selected!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))
    session['playlist_id'] = chosen_pl
    flash(f"Selected official playlist: {chosen_pl}", "info")
    return redirect(url_for('quiz_bp.import_spotify_playlist'))

########################################
# Let user pick from their own Spotify playlists
########################################
@quiz_bp.route('/select_own_playlist', methods=['GET','POST'])
@require_login
def select_own_playlist():
    sp = get_spotify_client()
    if not sp:
        flash("Connect your Spotify account first!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))
    if request.method == 'GET':
        try:
            user_playlists = []
            offset = 0
            while True:
                results = sp.current_user_playlists(limit=50, offset=offset)
                items = results.get('items', [])
                user_playlists.extend(items)
                if len(items) < 50:
                    break
                offset += 50
            return render_template('select_own_playlist.html', playlists=user_playlists)
        except spotipy.exceptions.SpotifyException as e:
            if "expired" in str(e).lower():
                flash("Your Spotify token expired. Please reconnect.", "info")
                return redirect(url_for('auth_bp.spotify_login'))
            else:
                flash(f"Error fetching your playlists: {str(e)}", "danger")
                return redirect(url_for('quiz_bp.dashboard'))
    chosen_pl = request.form.get('playlist_id')
    if not chosen_pl:
        flash("No playlist selected!", "warning")
        return redirect(url_for('quiz_bp.select_own_playlist'))
    session['playlist_id'] = chosen_pl
    flash(f"Selected playlist: {chosen_pl}", "info")
    return redirect(url_for('quiz_bp.import_spotify_playlist'))

########################################
# Import Spotify Playlist
########################################
@quiz_bp.route('/import_spotify_playlist', methods=['GET','POST'])
@require_login
def import_spotify_playlist():
    if request.method == 'GET':
        return render_template('import_spotify_playlist.html')
    user_playlist_id = request.form.get('playlist_id') or session.get('playlist_id')
    if not user_playlist_id:
        user_playlist_id = '37i9dQZF1DWSqmBTGDYngZ'  # default fallback
    max_tracks_str = request.form.get('max_tracks','')
    max_tracks = 9999
    if max_tracks_str.isdigit():
        max_tracks = int(max_tracks_str)
        if max_tracks < 1:
            max_tracks = 9999
    sp_user = get_spotify_client()
    if not sp_user:
        flash("No user token. Trying client credentials fallback...", "warning")
        sp_user = get_spotify_client_credentials()
        if not sp_user:
            flash("No Spotify credentials at all!", "danger")
            return redirect(url_for('quiz_bp.dashboard'))
    global ALL_TRACKS
    ALL_TRACKS.clear()
    total_tracks, actually_added = _fetch_playlist_tracks(sp_user, user_playlist_id, max_tracks)
    if actually_added == 0:
        sp_cc = get_spotify_client_credentials()
        if sp_cc and sp_cc != sp_user:
            print("[DEBUG] Trying fallback client creds for this playlist...")
            ALL_TRACKS.clear()
            total_tracks, actually_added = _fetch_playlist_tracks(sp_cc, user_playlist_id, max_tracks)
    skipped = total_tracks - actually_added
    flash(f"Fetched {total_tracks} total tracks; {actually_added} have previews (Skipped {skipped}).", "success")
    return redirect(url_for('quiz_bp.dashboard'))

def _fetch_playlist_tracks(sp, playlist_id, max_tracks=9999):
    offset = 0
    total_tracks = 0
    actually_added = 0
    global ALL_TRACKS
    while True:
        try:
            remaining = max_tracks - total_tracks
            if remaining <= 0:
                break
            batch_size = min(50, remaining)
            results = sp.playlist_items(playlist_id, limit=batch_size, offset=offset)
        except Exception as e:
            print(f"[DEBUG] Error fetching playlist offset={offset}: {e}")
            break
        items = results.get('items', [])
        if not items:
            break
        for item in items:
            track_obj = item.get('track')
            if not track_obj:
                continue
            if track_obj.get('is_local') or track_obj.get('type') != 'track':
                total_tracks += 1
                continue
            track_id = track_obj.get('id')
            title = track_obj.get('name','')
            artists = track_obj.get('artists', [])
            artist_name = artists[0]['name'] if artists else "Unknown"
            preview_url = track_obj.get('preview_url')
            if not preview_url and track_id:
                preview_url = _try_get_preview_for_track(sp, track_id, title)
            # Fallback: if still no preview, use the Spotify embed URL
            if not preview_url and track_id:
                preview_url = f"https://open.spotify.com/embed/track/{track_id}"
            if preview_url:
                album_info = track_obj.get('album', {})
                release_date = album_info.get('release_date','1900-01-01')
                year = 1900
                if release_date[:4].isdigit():
                    year = int(release_date[:4])
                ALL_TRACKS.append({
                    "id": track_id,
                    "title": title,
                    "artist": artist_name,
                    "preview_url": preview_url,
                    "year": year
                })
                actually_added += 1
            total_tracks += 1
        offset += batch_size
        if offset >= results.get('total', 0):
            break
    return total_tracks, actually_added

########################################
# Show environment check
########################################
@quiz_bp.route('/preview_environment_check')
@require_login
def preview_environment_check():
    txt = f"We currently have {len(ALL_TRACKS)} tracks with previews in memory.\n"
    txt += "Try /test_known_track to see if you can fetch a known snippet.\n"
    return txt

########################################
# SETTINGS
########################################
@quiz_bp.route('/settings', methods=['GET','POST'])
@require_login
def settings():
    if request.method == 'POST':
        session['color_theme'] = request.form.get('color_theme','navy')
        session['difficulty'] = request.form.get('difficulty','normal')
        custom_pl = request.form.get('playlist_id','').strip()
        if custom_pl:
            session['playlist_id'] = custom_pl
        flash("Settings updated!", "success")
        return redirect(url_for('quiz_bp.dashboard'))
    color = session.get('color_theme','navy')
    diff = get_difficulty()
    curr_pl = session.get('playlist_id','')
    return render_template('settings.html',
                           color_theme=color,
                           current_diff=diff,
                           current_playlist=curr_pl)

########################################
# DASHBOARD
########################################
@quiz_bp.route('/dashboard')
@require_login
def dashboard():
    user = current_user()
    missed_count = len(user.get_missed_songs())
    return render_template('dashboard.html',
                           user=user,
                           missed_count=missed_count,
                           all_songs=ALL_TRACKS)

########################################
# RANDOM MODE
########################################
@quiz_bp.route('/random_version')
@require_login
def random_version():
    if not ALL_TRACKS:
        flash("No snippet tracks yet. Please import a playlist!", "warning")
        return redirect(url_for('quiz_bp.dashboard'))
    chosen = random.choice(ALL_TRACKS)
    session["current_track_id"] = chosen["id"]
    ctype = random.choice(["artist", "title", "year"])  # or "both" if desired
    session["challenge_type"] = ctype
    return render_template('random_version.html', song=chosen, feedback=None)

########################################
# PERSONALIZED MODE (with adaptive difficulty)
########################################
@quiz_bp.route('/personalized_version')
@require_login
def personalized_version():
    if not ALL_TRACKS:
        flash("No snippet tracks yet. Please import a playlist!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))
    user = current_user()
    # Adaptive difficulty based on user accuracy:
    acc = user.get_accuracy()
    if acc > 0.8:
        session['difficulty'] = 'hard'
    elif acc < 0.3:
        session['difficulty'] = 'easy'
    else:
        session['difficulty'] = 'normal'
    missed = user.get_missed_songs()
    if missed:
        track_id = random.choice(missed)
        chosen = next((t for t in ALL_TRACKS if t["id"] == track_id), None)
        if not chosen:
            chosen = random.choice(ALL_TRACKS)
    else:
        chosen = random.choice(ALL_TRACKS)
    ctype = random.choice(["artist", "title", "year"])  # or "both"
    session["challenge_type"] = ctype
    session["current_track_id"] = chosen["id"]
    return render_template('personalized_version.html',
                           song=chosen,
                           missed_count=len(missed),
                           feedback=None)

########################################
# SUBMIT GUESS
########################################
@quiz_bp.route('/submit_guess', methods=['POST'])
@require_login
def submit_guess():
    user = current_user()
    track_id = session.get("current_track_id")
    challenge = session.get("challenge_type", "artist")
    if not track_id:
        flash("No active track. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))
    track = next((t for t in ALL_TRACKS if t["id"] == track_id), None)
    if not track:
        flash("Track not found. Returning to dashboard.", "warning")
        return redirect(url_for('quiz_bp.dashboard'))
    guess_correct = False
    # For 'both' challenge:
    guess_artist = request.form.get("guess_artist", "").strip().lower()
    guess_title = request.form.get("guess_title", "").strip().lower()
    if challenge == "artist":
        guess_str = request.form.get("guess", "").strip().lower()
        if guess_str == track["artist"].lower():
            guess_correct = True
    elif challenge == "title":
        guess_str = request.form.get("guess", "").strip().lower()
        if guess_str == track["title"].lower():
            guess_correct = True
    elif challenge == "year":
        guess_str = request.form.get("guess", "").strip()
        if guess_str.isdigit():
            guess_year = int(guess_str)
            if within_year_margin(guess_year, track["year"]):
                guess_correct = True
    elif challenge == "both":
        if (guess_artist == track["artist"].lower() and
            guess_title == track["title"].lower()):
            guess_correct = True
    user.total_attempts += 1
    missed_list = user.get_missed_songs()
    if guess_correct:
        user.total_correct += 1
        if track_id in missed_list:
            missed_list.remove(track_id)
        feedback = "Correct!"
    else:
        if track_id not in missed_list:
            missed_list.append(track_id)
        feedback = "Wrong! We'll re-ask this track."
    user.set_missed_songs(missed_list)
    db.session.commit()
    session["feedback"] = feedback
    source_page = request.form.get("source_page", "random")
    if source_page == "random":
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
