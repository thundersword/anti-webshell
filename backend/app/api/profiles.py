from flask import jsonify
from flask_jwt_extended import jwt_required

from ..executor import get_executor_for_node
from ..services.site_sync import profile_defaults
from . import api_bp


@api_bp.get("/profiles")
@jwt_required()
def list_profiles():
    result = get_executor_for_node("local").list_profiles()
    profiles = (result.data or {}).get("profiles") or ["thinkphp"]
    details = []
    for name in profiles:
        crit, white = profile_defaults(name, "{PROJECT_DIR}")
        details.append(
            {
                "name": name,
                "critical_files": [p.replace("{PROJECT_DIR}/", "") for p in crit],
                "whitelist_dirs": [p.replace("{PROJECT_DIR}/", "") for p in white],
            }
        )
    return jsonify(profiles=details)
