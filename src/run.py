from .app import create_app
import logging

logging.basicConfig(level=logging.DEBUG)

try:
    app = create_app()
    if __name__ == "__main__":
        app.run(debug=True)
except Exception as e:
    logging.exception("Failed to start the Flask application")
    raise
