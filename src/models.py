# models.py

from app import db
import json
from datetime import datetime

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)

    total_correct = db.Column(db.Integer, default=0)
    total_attempts = db.Column(db.Integer, default=0)
    missed_songs = db.Column(db.String(1000), default="[]")

    # ELO RATINGS
    random_guess_elo = db.Column(db.Integer, default=1200)
    personalized_guess_elo = db.Column(db.Integer, default=1200)
    random_rank_elo = db.Column(db.Integer, default=1200)
    personalized_rank_elo = db.Column(db.Integer, default=1200)

    # For demonstration, store user preferences in JSON (e.g., top-genre).
    preferences_json = db.Column(db.Text, default="{}")

    def get_preferences(self):
        """
        You might store user-specific preferences, e.g., favorite genres from Spotify's top artists.
        """
        try:
            return json.loads(self.preferences_json)
        except:
            return {}

    def set_preferences(self, prefs):
        self.preferences_json = json.dumps(prefs)

    def get_missed_songs(self):
        try:
            return json.loads(self.missed_songs)
        except:
            return []

    def set_missed_songs(self, arr):
        self.missed_songs = json.dumps(arr)

    def get_accuracy(self):
        if self.total_attempts == 0:
            return 0
        return self.total_correct / self.total_attempts

    def get_level(self):
        """
        Example classification based on accuracy:
        """
        acc = self.get_accuracy()
        if acc < 0.3:
            return "beginner"
        elif acc < 0.8:
            return "intermediate"
        else:
            return "advanced"

    def update_elo(self, approach, mode, outcome):
        """
        approach='random' or 'personalized'
        mode='guess' or 'rank'
        outcome=1.0 for success, 0.0 for failure, or any 0..1 fraction
        """
        K = 32
        if approach == 'random' and mode == 'guess':
            old_elo = self.random_guess_elo
            new_elo = old_elo + K * (outcome - 0.5)
            self.random_guess_elo = round(new_elo)

        elif approach == 'personalized' and mode == 'guess':
            old_elo = self.personalized_guess_elo
            new_elo = old_elo + K * (outcome - 0.5)
            self.personalized_guess_elo = round(new_elo)

        elif approach == 'random' and mode == 'rank':
            old_elo = self.random_rank_elo
            new_elo = old_elo + K * (outcome - 0.5)
            self.random_rank_elo = round(new_elo)

        elif approach == 'personalized' and mode == 'rank':
            old_elo = self.personalized_rank_elo
            new_elo = old_elo + K * (outcome - 0.5)
            self.personalized_rank_elo = round(new_elo)


class GuessLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    track_id = db.Column(db.String(80), nullable=False)
    question_type = db.Column(db.String(10))  # "artist", "title", or "year"
    is_correct = db.Column(db.Boolean, default=False)
    time_taken = db.Column(db.Float, default=0.0)
    approach = db.Column(db.String(15))  # "random" or "personalized"
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship back to user
    user = db.relationship("User", backref="guess_logs")
