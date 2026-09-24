"""Компактные результаты GitHub для контекста языковой модели."""

from typing import Any


def _select(data: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data}


def _user(data: dict[str, Any] | None) -> dict[str, Any] | None:
    return _select(data, "login", "html_url") if data is not None else None


def repository(data: dict[str, Any]) -> dict[str, Any]:
    result = _select(
        data, "name", "full_name", "description", "html_url", "homepage",
        "language", "topics", "stargazers_count", "forks_count", "open_issues_count",
        "default_branch", "private", "archived", "fork", "visibility",
        "created_at", "updated_at", "pushed_at",
    )
    if "owner" in data:
        result["owner"] = _user(data["owner"])
    if "license" in data:
        license_data = data["license"]
        result["license"] = _select(license_data, "name", "spdx_id") if license_data else None
    return result


def issue(data: dict[str, Any], *, details: bool = False) -> dict[str, Any]:
    result = _select(
        data, "number", "title", "state", "state_reason", "html_url",
        "created_at", "updated_at", "closed_at", "comments",
    )
    if details and "body" in data:
        result["body"] = data["body"]
    if "user" in data:
        result["user"] = _user(data["user"])
    if "assignees" in data:
        result["assignees"] = [_user(user) for user in data["assignees"]]
    if "labels" in data:
        result["labels"] = [
            _select(label, "name", "description") if isinstance(label, dict) else label
            for label in data["labels"]
        ]
    if "milestone" in data:
        milestone = data["milestone"]
        result["milestone"] = _select(milestone, "number", "title", "state") if milestone else None
    return result


def pull_request(data: dict[str, Any], *, details: bool = False) -> dict[str, Any]:
    result = issue(data, details=details)
    result.update(_select(
        data, "draft", "merged", "merged_at", "mergeable", "mergeable_state",
        "merge_commit_sha", "commits", "additions", "deletions", "changed_files", "review_comments",
    ))
    for branch in ("head", "base"):
        if branch in data and data[branch] is not None:
            result[branch] = _select(data[branch], "label", "ref", "sha")
    return result


def content(data: dict[str, Any]) -> dict[str, Any]:
    return _select(
        data, "type", "name", "path", "size", "sha", "html_url", "download_url",
        "target", "submodule_git_url", "content", "encoding",
    )
