import re
import random
import spotipy
import spotipy.exceptions
from flask import Blueprint, session, redirect, url_for, flash
from functools import wraps
from app import db
from models import User

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
    """
    Accepts a Spotify playlist ID, full link, or URI. Returns the extracted ID.
    """
    if not user_input:
        return ""
    user_input = user_input.strip()
    match = re.search(r'(?:spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)', user_input)
    if match:
        return match.group(1)
    if re.match(r'^[A-Za-z0-9]+$', user_input):
        return user_input
    return user_input

def get_buddy_personality_lines():
    """
    Returns lines based on buddy_personality in session, default='friendly'.
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
            ],
            'rank': [
                "Ranking time! Let's see what you think!",
                "Time to order some tracks—have fun!"
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
            ]
        }
    }
    return lines.get(personality, lines['friendly'])

########################################
# FETCH PLAYLIST TRACKS (Popularity + Year)
########################################
def _fetch_playlist_tracks(sp, playlist_id, limit=300):
    """
    Retrieve tracks from a Spotify playlist. 
    We store year & popularity for ranking logic, plus preview_url if available.
    """
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
