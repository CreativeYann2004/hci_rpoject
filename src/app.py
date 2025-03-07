from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
import datetime
import os
from dotenv import load_dotenv

db = SQLAlchemy()

def create_app():
    load_dotenv()
    app = Flask(__name__)
    # For demo; use a secure random key in production
    app.secret_key = "ANY_RANDOM_SECRET_FOR_DEVELOPMENT"

    # Database config
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///guessing_game.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    @app.context_processor
    def inject_now():
        return {"now": datetime.datetime.now()}

    @app.context_processor
    def inject_buddy_message():
        return {
            "buddy_message": session.get("buddy_message", ""),
            "buddy_hint": session.get("buddy_hint", "")
        }

    # Register Blueprints
    from src.routes.auth import auth_bp
    from src.routes.quiz_base import quiz_bp

    import src.routes.quiz_main
    import src.routes.quiz_personalized
    import src.routes.quiz_ranking

    app.register_blueprint(auth_bp)
    app.register_blueprint(quiz_bp)

    with app.app_context():
        db.create_all()

    return app
