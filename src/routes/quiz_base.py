import random
import re
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
    Returns a dict of lines based on session's buddy_personality, default='friendly'.
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
# OPTIONAL: PERSONALIZATION SETTINGS
########################################
def get_personalization_settings(level):
    """
    For older personalized approach (year_margin, snippet_seconds, hints).
    """
    if level == "beginner":
        return {"year_margin": 3, "snippet_seconds": 30, "show_hints": True}
    elif level == "intermediate":
        return {"year_margin": 1, "snippet_seconds": 15, "show_hints": False}
    else:  # advanced
        return {"year_margin": 0, "snippet_seconds": 5, "show_hints": False}
