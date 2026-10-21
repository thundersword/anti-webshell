from flask import jsonify, request
from flask_jwt_extended import jwt_required

from ..services.profiles import detect_profile, load_all_profiles, profile_rel_defaults
from . import api_bp


@api_bp.get("/profiles")
@jwt_required()
def list_profiles():
    details = []
    for name, spec in load_all_profiles().items():
        crit, white = profile_rel_defaults(name)
        details.append(
            {
                "name": name,
                "label": spec.get("DISPLAY_NAME") or name,
                "critical_files": crit,
                "whitelist_dirs": white,
                "inotify_exclude": spec.get("INOTIFY_EXCLUDE") or "",
            }
        )
    return jsonify(profiles=details)


@api_bp.get("/profiles/detect")
@jwt_required()
def detect():
    project_dir = (request.args.get("project_dir") or "").strip()
    if not project_dir:
        return jsonify(error="project_dir 必填"), 400
    return jsonify(detect_profile(project_dir))
