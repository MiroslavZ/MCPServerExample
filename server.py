"""MCP-сервер GitHub: python server.py, Streamable HTTP на /mcp."""

import base64
import binascii
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import github_api


mcp = MCPServer("github", instructions="Поиск репозиториев, чтение файлов, issues и pull requests через GitHub API.")
read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

Owner = Annotated[str, Field(description="Логин владельца или организации GitHub", pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$", max_length=100)]
Repo = Annotated[str, Field(description="Имя репозитория без владельца", pattern=r"^(\.[A-Za-z0-9_-]|[A-Za-z0-9_-])[A-Za-z0-9_.-]*$", max_length=100)]
IssueNumber = Annotated[int, Field(description="Номер issue в репозитории", ge=1)]
Page = Annotated[int, Field(description="Номер страницы, начиная с 1", ge=1)]
PerPage = Annotated[int, Field(description="Размер страницы GitHub API, от 1 до 100", ge=1, le=100)]


async def _get(path: str, **params: Any) -> tuple[Any, bool]:
    try:
        return await github_api.get(path, **params)
    except github_api.GitHubError as error:
        raise ToolError(str(error)) from None


@mcp.tool(annotations=read_only)
async def get_repository(owner: Owner, repo: Repo) -> dict[str, Any]:
    """Получить сведения о репозитории: описание, URL, язык, звёзды и ветку по умолчанию."""
    data, _ = await _get(f"/repos/{owner}/{repo}")
    return data


@mcp.tool(annotations=read_only)
async def list_issues(
    owner: Owner,
    repo: Repo,
    state: Annotated[Literal["open", "closed", "all"], Field(description="Состояние issues")] = "open",
    page: Page = 1,
    per_page: PerPage = 20,
) -> dict[str, Any]:
    """Получить страницу issues без pull requests, от новых к старым.

    GitHub считает pull requests при пагинации: issues может быть меньше per_page
    или даже ноль. Для продолжения используйте next_page, а не длину issues.
    """
    data, has_next = await _get(
        f"/repos/{owner}/{repo}/issues", state=state, page=page,
        per_page=per_page, sort="created", direction="desc",
    )
    return {
        "issues": [item for item in data if "pull_request" not in item],
        "page": page,
        "next_page": page + 1 if has_next else None,
    }


@mcp.tool(annotations=read_only)
async def get_issue(owner: Owner, repo: Repo, issue_number: IssueNumber) -> dict[str, Any]:
    """Получить issue по номеру, включая описание, автора, метки и состояние."""
    data, _ = await _get(f"/repos/{owner}/{repo}/issues/{issue_number}")
    if "pull_request" in data:
        raise ToolError("Этот номер относится к pull request, а не issue.")
    return data


@mcp.tool(annotations=read_only)
async def search_repositories(
    query: Annotated[str, Field(description="Запрос GitHub Search, например language:python stars:>100", min_length=1, max_length=256)],
    page: Page = 1,
    per_page: PerPage = 20,
) -> dict[str, Any]:
    """Найти репозитории через GitHub Search. Доступны первые 1000 результатов."""
    if not query.strip():
        raise ToolError("Поисковый запрос не должен быть пустым.")
    if page * per_page > 1000:
        raise ToolError("GitHub Search позволяет получить только первые 1000 результатов.")
    data, has_next = await _get("/search/repositories", q=query, page=page, per_page=per_page)
    return {
        "repositories": data["items"],
        "total_count": data["total_count"],
        "incomplete_results": data["incomplete_results"],
        "page": page,
        "next_page": page + 1 if has_next and (page + 1) * per_page <= 1000 else None,
    }


@mcp.tool(annotations=read_only)
async def list_pull_requests(
    owner: Owner,
    repo: Repo,
    state: Annotated[Literal["open", "closed", "all"], Field(description="Состояние pull requests")] = "open",
    page: Page = 1,
    per_page: PerPage = 20,
) -> dict[str, Any]:
    """Получить страницу pull requests от новых к старым, включая автора и ветки."""
    data, has_next = await _get(
        f"/repos/{owner}/{repo}/pulls", state=state, page=page,
        per_page=per_page, sort="created", direction="desc",
    )
    return {"pull_requests": data, "page": page, "next_page": page + 1 if has_next else None}


@mcp.tool(annotations=read_only)
async def get_pull_request(
    owner: Owner,
    repo: Repo,
    pull_number: Annotated[int, Field(description="Номер pull request в репозитории", ge=1)],
) -> dict[str, Any]:
    """Получить pull request: описание, ветки, состояние слияния и статистику изменений."""
    data, _ = await _get(f"/repos/{owner}/{repo}/pulls/{pull_number}")
    return data


@mcp.tool(annotations=read_only)
async def get_repository_content(
    owner: Owner,
    repo: Repo,
    path: Annotated[str, Field(description="Путь файла или каталога внутри репозитория; пустая строка — корень", max_length=4096)] = "",
    ref: Annotated[str | None, Field(description="Ветка, тег или SHA коммита; по умолчанию основная ветка", min_length=1)] = None,
) -> dict[str, Any]:
    """Показать каталог или прочитать UTF-8 файл до 1 МБ.

    Бинарные файлы возвращаются в base64. Каталог содержит максимум 1000 записей
    (ограничение GitHub Contents API), без рекурсивного обхода.
    """
    if path and (path.startswith("/") or "\\" in path or "\x00" in path
                 or any(part in {".", "..", ""} for part in path.split("/"))):
        raise ToolError("Укажите относительный путь без '.', '..', пустых сегментов и обратных слешей.")
    params = {"ref": ref} if ref is not None else {}
    data, _ = await _get(f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}", **params)
    if isinstance(data, list):
        return {"type": "directory", "entries": data, "limit_reached": len(data) >= 1000}
    if data.get("type") != "file":
        return data
    if data.get("size", 0) > 1_000_000 or data.get("encoding") != "base64":
        raise ToolError("Чтение содержимого поддерживается только для файлов до 1 МБ в base64.")
    try:
        raw = base64.b64decode("".join(data["content"].split()), validate=True)
    except (binascii.Error, KeyError, ValueError):
        raise ToolError("GitHub вернул некорректное содержимое файла.") from None
    try:
        return {**data, "content": raw.decode("utf-8"), "encoding": "utf-8"}
    except UnicodeDecodeError:
        return data


if __name__ == "__main__":
    from http_server import run

    run(mcp)
