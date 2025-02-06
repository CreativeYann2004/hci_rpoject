from app import db
import json

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)

    total_correct = db.Column(db.Integer, default=0)
    total_attempts = db.Column(db.Integer, default=0)
    missed_songs = db.Column(db.String(1000), default="[]")

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
        Compute the user's level on-the-fly, based on accuracy.
        Or you could store it in the DB and update occasionally.
        """
        acc = self.get_accuracy()
        if acc < 0.3:
            return "beginner"
        elif acc < 0.8:
            return "intermediate"
        else:
            return "advanced"
