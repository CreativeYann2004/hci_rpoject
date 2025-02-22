# routes/auth.py
import requests
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash
)
from urllib.parse import urlencode
from app import db
from models import User
import spotipy
import os

auth_bp = Blueprint('auth_bp', __name__)

########################
# SCOPES
########################
SPOTIFY_SCOPES = (
    "user-read-private "
    "user-read-email "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-public "
    "playlist-modify-private "
    "user-read-recently-played"
)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5000/spotify/callback")

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

@auth_bp.route('/')
def home():
    if current_user():
        return redirect(url_for('quiz_bp.dashboard'))
    else:
        return redirect(url_for('auth_bp.login'))

@auth_bp.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        un = request.form.get('username')
        pw = request.form.get('password')
        if not un or not pw:
            flash("Username/password cannot be empty!", "danger")
            return redirect(url_for('auth_bp.register'))
        existing = User.query.filter_by(username=un).first()
        if existing:
            flash("Username already taken!", "danger")
            return redirect(url_for('auth_bp.register'))
        newu = User(username=un, password=pw)
        db.session.add(newu)
        db.session.commit()
        flash("Registered! Please log in.", "success")
        return redirect(url_for('auth_bp.login'))
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        un = request.form.get('username')
        pw = request.form.get('password')
        user = User.query.filter_by(username=un).first()
        if user and user.password == pw:
            session['user_id'] = user.id
            flash("Login successful!", "success")
            return redirect(url_for('quiz_bp.dashboard'))
        else:
            flash("Invalid credentials!", "danger")
            return redirect(url_for('auth_bp.login'))
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('spotify_token', None)
    session.pop('buddy_hint', None)  # Clear buddy hint too
    flash("Logged out!", "info")
    return redirect(url_for('auth_bp.login'))

def get_spotify_token():
    return session.get("spotify_token")

def get_spotify_client():
    token = get_spotify_token()
    if not token:
        return None
    return spotipy.Spotify(auth=token)

@auth_bp.route('/spotify/login')
@require_login
def spotify_login():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        flash("Spotify credentials not set!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SPOTIFY_SCOPES
    }
    url = "https://accounts.spotify.com/authorize?" + urlencode(params)
    return redirect(url)

@auth_bp.route('/spotify/callback')
def spotify_callback():
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        flash(f"Spotify auth error: {error}", "danger")
        return redirect(url_for('quiz_bp.dashboard'))
    token_url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET
    }
    resp = requests.post(token_url, data=data)
    if resp.status_code != 200:
        flash(f"Spotify token request failed: {resp.text}", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    token_info = resp.json()
    access_token = token_info.get("access_token")
    if not access_token:
        flash("No access token from Spotify!", "danger")
        return redirect(url_for('quiz_bp.dashboard'))

    session["spotify_token"] = access_token
    flash("Spotify connected! You can now import your playlists or view recent tracks.", "success")
    return redirect(url_for('quiz_bp.dashboard'))
