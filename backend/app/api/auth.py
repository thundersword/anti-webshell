from flask import jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from ..models import User
from . import api_bp


@api_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify(error="用户名和密码必填"), 400

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify(error="用户名或密码错误"), 401

    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return jsonify(access_token=token, user=user.to_dict())


@api_bp.get("/auth/me")
@jwt_required()
def me():
    user = User.query.get(int(get_jwt_identity()))
    if not user:
        return jsonify(error="用户不存在"), 404
    return jsonify(user.to_dict())
