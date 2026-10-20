from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager

from .config import Config
from .models import Node, User, db


def create_app(config_object=Config):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_object(config_object)
    if hasattr(config_object, "init_from_env"):
        config_object.init_from_env(app)
    # Allow explicit overrides from subclass attributes set in tests
    for key in (
        "SQLALCHEMY_DATABASE_URI",
        "WEBSHELL_DRY_RUN",
        "WEBSHELL_CONFIG_ROOT",
        "WEBSHELL_INSTALL_DIR",
    ):
        if hasattr(config_object, key):
            val = getattr(config_object, key)
            if val is not None:
                app.config[key] = val
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(
        seconds=int(app.config.get("JWT_ACCESS_TOKEN_EXPIRES", 86400))
    )

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    JWTManager(app)

    from .api import api_bp

    app.register_blueprint(api_bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    with app.app_context():
        db.create_all()
        _bootstrap(app)

    return app


def _bootstrap(app):
    if not Node.query.get("local"):
        db.session.add(Node(id="local", name="Local Host", kind="local"))
        db.session.commit()

    admin_user = app.config.get("DEFAULT_ADMIN_USER", "admin")
    admin_pass = app.config.get("DEFAULT_ADMIN_PASSWORD", "admin123")
    if not User.query.filter_by(username=admin_user).first():
        user = User(username=admin_user, role="admin")
        user.set_password(admin_pass)
        db.session.add(user)
        db.session.commit()
