"""Небольшой асинхронный клиент GitHub REST API."""

import os
from pathlib import Path
from typing import Any

import httpx2 as httpx
from dotenv import load_dotenv


class GitHubError(Exception):
    """Ошибка API с безопасным для пользователя сообщением."""


async def get(path: str, **params: Any) -> tuple[Any, bool]:
    """Вернуть JSON и признак следующей страницы; токен не попадает в ошибки."""
    load_dotenv(Path(__file__).with_name(".env"))
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "github-mcp-example",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(
            base_url="https://api.github.com", headers=headers, timeout=20,
        ) as client:
            response = await client.get(path, params=params)
    except httpx.RequestError:
        raise GitHubError("Не удалось связаться с GitHub. Проверьте сеть и повторите запрос.") from None

    if response.status_code != 200:
        if response.status_code == 429 or (
            response.status_code == 403
            and (response.headers.get("X-RateLimit-Remaining") == "0"
                 or "Retry-After" in response.headers)
        ):
            message = "Превышен лимит GitHub API. Повторите запрос позже."
        else:
            message = {
                401: "GitHub отклонил токен. Проверьте GITHUB_TOKEN в .env.",
                403: "GitHub запретил доступ: проверьте права токена или лимит запросов.",
                404: "Ресурс GitHub не найден либо недоступен этому токену.",
                301: "Репозиторий перемещён. Укажите его актуальные owner и repo.",
                422: "GitHub отклонил параметры запроса.",
            }.get(response.status_code, "GitHub не смог выполнить запрос. Повторите позже.")
        raise GitHubError(f"{message} HTTP {response.status_code}.")
    try:
        return response.json(), "next" in response.links
    except ValueError:
        raise GitHubError("GitHub вернул некорректный JSON.") from None
