from flask import Blueprint

api_bp = Blueprint("api", __name__)

from . import auth, config, events, profiles, service, sites  # noqa: E402,F401
