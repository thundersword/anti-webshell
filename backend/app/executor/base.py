"""Executor interface: control-plane operations against a node.

LocalSystemdExecutor implements this today.
RemoteAgentExecutor is reserved for multi-host (HTTPS + node token).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SiteRuntimeConfig:
    site_id: str
    project_dir: str
    profile: str = "thinkphp"
    auto_restore: bool = True
    lock_critical_files: bool = True
    alert_email: str = ""
    wechat_webhook: str = ""
    dingtalk_webhook: str = ""
    critical_files: List[str] = field(default_factory=list)
    whitelist_dirs: List[str] = field(default_factory=list)


@dataclass
class ExecResult:
    ok: bool
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    data: Optional[dict] = None


class Executor(ABC):
    @abstractmethod
    def write_config(self, cfg: SiteRuntimeConfig) -> ExecResult:
        ...

    @abstractmethod
    def start_site(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def stop_site(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def restart_site(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def site_status(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def remove_site_config(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def tail_logs(self, site_id: str, lines: int = 100) -> ExecResult:
        ...

    @abstractmethod
    def list_quarantine(self, site_id: str) -> ExecResult:
        ...

    @abstractmethod
    def list_profiles(self) -> ExecResult:
        ...
