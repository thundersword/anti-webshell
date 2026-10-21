"""Load engine profiles from scripts/profiles/*.conf and detect framework."""

from __future__ import annotations

import os
import re
from pathlib import Path

from flask import current_app

_KV_DQ = re.compile(r'^([A-Z_]+)="([^"]*)"', re.M)
_KV_SQ = re.compile(r"^([A-Z_]+)='([^']*)'", re.M)
_ARR = re.compile(r"^([A-Z_]+)=\((.*?)\)", re.S | re.M)

FALLBACK = {
    "plain_php": {
        "DISPLAY_NAME": "纯 PHP",
        "CRITICAL_REL_PATHS": ["index.php"],
        "WHITELIST_REL_DIRS": ["cache", "tmp", "temp"],
        "INOTIFY_EXCLUDE": r"(\.git|\.svn|\.hg|node_modules|/vendor/)",
    }
}


def profiles_dir() -> Path:
    install = None
    try:
        install = current_app.config.get("WEBSHELL_INSTALL_DIR")
    except RuntimeError:
        install = None
    install = install or os.getenv("WEBSHELL_INSTALL_DIR", "")
    path = Path(install) / "profiles" if install else Path()
    if path.is_dir():
        return path
    repo = Path(__file__).resolve().parents[3] / "scripts" / "profiles"
    return repo


def parse_profile_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    data = {
        "PROFILE_NAME": path.stem,
        "DISPLAY_NAME": path.stem,
        "CRITICAL_REL_PATHS": [],
        "WHITELIST_REL_DIRS": [],
        "INOTIFY_EXCLUDE": r"(\.git|\.svn|\.hg|node_modules|/vendor/)",
    }
    for m in _KV_DQ.finditer(text):
        data[m.group(1)] = m.group(2)
    for m in _KV_SQ.finditer(text):
        data[m.group(1)] = m.group(2)
    for m in _ARR.finditer(text):
        data[m.group(1)] = re.findall(r'"([^"]+)"', m.group(2))
    return data


def load_all_profiles() -> dict:
    d = profiles_dir()
    found = {}
    if d.is_dir():
        for conf in sorted(d.glob("*.conf")):
            found[conf.stem] = parse_profile_file(conf)
    if not found:
        found = {k: dict(v) for k, v in FALLBACK.items()}
    return found


def profile_rel_defaults(profile: str) -> tuple[list[str], list[str]]:
    all_p = load_all_profiles()
    spec = all_p.get(profile) or all_p.get("thinkphp5") or all_p.get("plain_php") or {}
    return (
        list(spec.get("CRITICAL_REL_PATHS") or ["index.php"]),
        list(spec.get("WHITELIST_REL_DIRS") or []),
    )


def profile_defaults(profile: str, project_dir: str) -> tuple[list[str], list[str]]:
    crit, white = profile_rel_defaults(profile)
    root = project_dir.rstrip("/")

    def abs_paths(rels):
        return [p if str(p).startswith("/") else f"{root}/{p}" for p in rels]

    return abs_paths(crit), abs_paths(white)


def detect_profile(project_dir: str) -> dict:
    root = Path(project_dir)
    if not root.is_dir():
        return {
            "profile": "plain_php",
            "confidence": "none",
            "reason": "项目目录不存在",
            "display_name": "纯 PHP",
        }

    def has(*parts):
        return (root.joinpath(*parts)).exists()

    if has("wp-config.php") or (root / "wp-includes").is_dir():
        return {
            "profile": "wordpress",
            "confidence": "high",
            "reason": "检测到 wp-config.php 或 wp-includes",
            "display_name": "WordPress",
        }
    if has("artisan") and has("bootstrap", "app.php"):
        return {
            "profile": "laravel",
            "confidence": "high",
            "reason": "检测到 artisan 与 bootstrap/app.php",
            "display_name": "Laravel",
        }
    if has("thinkphp", "base.php") and (root / "application").is_dir():
        return {
            "profile": "thinkphp5",
            "confidence": "high",
            "reason": "检测到 thinkphp/base.php 与 application/（ThinkPHP 5.0/5.1）",
            "display_name": "ThinkPHP 5.x",
        }
    if (root / "app").is_dir() and has("config", "app.php") and not (root / "thinkphp").is_dir():
        return {
            "profile": "thinkphp6",
            "confidence": "high",
            "reason": "检测到 app/ + config/app.php，无 thinkphp/ 目录（ThinkPHP 6/8）",
            "display_name": "ThinkPHP 6.x / 8.x",
        }
    if has("ThinkPHP", "ThinkPHP.php") or (root / "Application").is_dir():
        return {
            "profile": "thinkphp3",
            "confidence": "high" if has("ThinkPHP", "ThinkPHP.php") else "medium",
            "reason": "检测到 ThinkPHP/ThinkPHP.php 或 Application/（ThinkPHP 3.x）",
            "display_name": "ThinkPHP 3.x",
        }
    return {
        "profile": "plain_php",
        "confidence": "low",
        "reason": "未匹配到已知框架特征，使用纯 PHP 最小配置",
        "display_name": "纯 PHP",
    }
