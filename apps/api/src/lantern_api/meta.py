"""Public client configuration and the analysis settings in use."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends

from lantern_api.auth import current_user, services
from lantern_api.services import Services
from lantern_decisions.question_sets import available_versions
from lantern_platform.db import User
from lantern_registry import load_registry
from lantern_report.findings import DEFAULT_THRESHOLD
from lantern_report.severity import default_table
from lantern_report.statutes import default_map

router = APIRouter()


@router.get("/config")
def config(svc: Services = Depends(services)) -> dict[str, Any]:
    """What the web app needs before sign-in: where to install the GitHub App."""
    return {"install_url": svc.github.install_url(), "app_slug": svc.settings.github_app_slug}


@router.get("/settings")
def analysis_settings(
    user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    versions = available_versions()
    return {
        "threshold": DEFAULT_THRESHOLD,
        "question_set_version": versions[-1] if versions else None,
        "question_set_versions": versions,
        "registry_version": load_registry().version,
        "severity_version": default_table().version,
        "statutes_version": default_map().version,
        "decision_provider": os.environ.get("LANTERN_DECISION_PROVIDER", "stub"),
        "runs_per_hour": svc.settings.runs_per_hour,
        "run_timeout_s": svc.settings.run_timeout_s,
        "clone_max_mb": svc.settings.clone_max_mb,
    }
