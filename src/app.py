import os
import datetime
from flask import Flask
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

    # Provide "now" to all templates so you can use {{ now().year }}
    @app.context_processor
    def inject_now():
        return {"now": datetime.datetime.now}

    # Register Blueprints
    from routes.auth import auth_bp
    from routes.quiz import quiz_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(quiz_bp)

    # Create tables if needed
    with app.app_context():
        db.create_all()

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
