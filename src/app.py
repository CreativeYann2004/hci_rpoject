import os
import datetime
from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

db = SQLAlchemy()

def create_app():
    # Load environment variables from .env
    load_dotenv()

    app = Flask(__name__)
    app.secret_key = "ANY_RANDOM_SECRET_FOR_DEVELOPMENT"  # For dev only

    # Configure database
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///guessing_game.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    # Provide "now" to all templates (e.g. {{ now().year }})
    @app.context_processor
    def inject_now():
        return {"now": datetime.datetime.now}

    # Provide a default buddy_message so that every template has it (avoids Undefined errors)
    @app.context_processor
    def inject_buddy_message():
        return {"buddy_message": session.get("buddy_message", "")}

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.quiz import quiz_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(quiz_bp)

    # Create tables if they don’t exist
    with app.app_context():
        db.create_all()

    return app
