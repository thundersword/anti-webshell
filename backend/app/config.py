import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_SCRIPTS = BASE_DIR.parent / "scripts"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-please-use-long-key")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-change-me-please-use-long-key")
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES", "86400"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "admin")
    DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

    @staticmethod
    def init_from_env(app):
        """Bind env-dependent settings after dotenv / test env are ready."""
        database_url = os.getenv(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / 'webshell.db'}",
        )
        install_dir = os.getenv(
            "WEBSHELL_INSTALL_DIR",
            str(REPO_SCRIPTS if REPO_SCRIPTS.exists() else Path("/opt/webshell_monitor")),
        )
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["WEBSHELL_INSTALL_DIR"] = install_dir
        app.config["WEBSHELL_CONFIG_ROOT"] = os.getenv(
            "WEBSHELL_CONFIG_ROOT",
            "/etc/webshell-monitor/sites",
        )
        app.config["WEBSHELL_DRY_RUN"] = os.getenv("WEBSHELL_DRY_RUN", "0") == "1"
        app.config["PROFILES_DIR"] = os.getenv(
            "PROFILES_DIR",
            str(Path(install_dir) / "profiles"),
        )
        app.config["DEFAULT_ADMIN_USER"] = os.getenv("DEFAULT_ADMIN_USER", "admin")
        app.config["DEFAULT_ADMIN_PASSWORD"] = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
