from flask import Flask
from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local", override=True)

from app.db import db_session
from app.db import init_db
from app.routes import bp


def create_app():
	app = Flask(__name__)
	app.config.from_prefixed_env()
	app.secret_key = app.config.get("SECRET_KEY")

	if not app.secret_key:
		import os
		app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")

	app.register_blueprint(bp)

	@app.cli.command("init-db")
	def init_db_command():
		init_db()
		print("Initialized database tables.")

	@app.teardown_appcontext
	def shutdown_session(exception=None):
		db_session.remove()

	return app
