# GitHub MCP Server

Учебный MCP-сервер на Python 3.11+ и `mcp==2.2.0`. Предоставляет семь инструментов
для чтения GitHub API через **Streamable HTTP**. Endpoint: `/mcp`.
Агент может работать на другом компьютере; сервер запускается отдельно.

## Инструменты

| Инструмент | Параметры | Результат |
| --- | --- | --- |
| `get_repository` | `owner`, `repo` | Данные репозитория: описание, URL, язык, звёзды и другие поля GitHub |
| `search_repositories` | `query`, `page=1`, `per_page=20` | `repositories`, `total_count`, `incomplete_results`, `page`, `next_page` |
| `list_issues` | `owner`, `repo`, `state="open"`, `page=1`, `per_page=20` | `issues`, `page`, `next_page` |
| `get_issue` | `owner`, `repo`, `issue_number` | Описание, автор, метки и состояние issue |
| `list_pull_requests` | `owner`, `repo`, `state="open"`, `page=1`, `per_page=20` | `pull_requests`, `page`, `next_page` |
| `get_pull_request` | `owner`, `repo`, `pull_number` | Описание, ветки, состояние слияния и статистика PR |
| `get_repository_content` | `owner`, `repo`, `path=""`, `ref=null` | Содержимое файла либо каталог с `entries` и `limit_reached` |

`owner` — пользователь/организация, `repo` — имя репозитория без владельца.
`state`: `open`, `closed`, `all`; номера и `page` ≥ 1; `per_page`: 1–100.
`query` поддерживает синтаксис GitHub Search, например `language:python stars:>100`.
`ref` — ветка, тег или SHA коммита; без него используется основная ветка.
Типы, описания и ограничения параметров доступны через MCP `tools/list`.
Инструменты зарегистрированы декораторами `@mcp.tool()` и возвращают структурированный JSON.

Ограничения:

- Issues не включают PR. GitHub учитывает PR при пагинации, поэтому даже пустая
  страница может иметь продолжение. Следуйте по `next_page` до `null`.
- `get_issue` отклоняет номера PR. Комментарии и diff отдельными запросами не загружаются.
- GitHub Search позволяет просматривать первые 1000 результатов. Сервер принимает
  страницы, верхняя граница которых не превышает 1000; для полного обхода используйте
  размер страницы 20, 50 или 100. `incomplete_results=true` означает неполную выдачу GitHub.
- Чтение файлов ограничено 1 000 000 байтами. UTF-8 декодируется в текст, остальные
  файлы возвращаются в base64. Каталог — максимум 1000 записей без рекурсивного обхода;
  `limit_reached=true` означает, что список может быть неполным.
- Ошибки API возвращаются как MCP tool errors (`is_error=True` в Python SDK).

## Установка и локальный запуск

PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# Только если .env ещё нет:
Copy-Item .env.example .env
.\venv\Scripts\python.exe server.py
```

Linux (в том числе AWS EC2):

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
# Только если .env ещё нет:
cp .env.example .env
venv/bin/python server.py
```

По умолчанию endpoint — `http://127.0.0.1:8000/mcp`. Остановка — Ctrl+C.
Все настройки читаются из `.env` рядом с исходниками независимо от рабочей директории.
Переменные окружения имеют приоритет. После изменения настроек перезапустите сервер.
`.env` исключён из Git; существующий файл с токенами не перезаписывайте.

## Токен GitHub

Для публичных данных `GITHUB_TOKEN` необязателен. Для приватных репозиториев создайте
GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens.
Выберите нужные репозитории и разрешения чтения: **Metadata**, **Issues**,
**Pull requests**, **Contents** (в зависимости от используемых инструментов).

```dotenv
GITHUB_TOKEN=ваш_github_токен
```

Токен остаётся на сервере. Он определяет, какие данные доступны всем подключённым
к этому экземпляру агентам. Без авторизации лимиты GitHub ниже.

## Настройка удалённого доступа

Пример `.env` для VM за HTTPS reverse proxy (подставьте свой домен):

```dotenv
GITHUB_TOKEN=ваш_github_токен
MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_ACCESS_TOKEN=отдельный_случайный_секрет
MCP_ALLOWED_HOSTS=mcp.example.com,mcp.example.com:443,127.0.0.1:*,localhost:*
MCP_ALLOWED_ORIGINS=
```

Сгенерировать секрет можно командой `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
`MCP_ACCESS_TOKEN` — отдельный общий секрет между агентом и MCP-сервером, не GitHub-токен.
Клиент передаёт его как `Authorization: Bearer <MCP_ACCESS_TOKEN>`.
При сетевом bind (например, `0.0.0.0`) сервер требует непустой секрет.
На localhost он необязателен; если задан, проверяется и локально.
Без корректного секрета сервер возвращает HTTP 401.

Для публичного доступа настройте HTTPS reverse proxy (например, Nginx/Caddy) на
порт 8000. В AWS Security Group разрешите HTTPS на 443, а внутренний порт 8000
оставьте доступным только proxy. Proxy должен сохранять заголовки `Authorization`
и `Host`. Если proxy на той же VM, можно использовать `MCP_HOST=127.0.0.1`,
сохранив `MCP_ACCESS_TOKEN`.

`MCP_ALLOWED_HOSTS` содержит значения HTTP Host без схемы и пути, через запятую.
Для доступа по IP в закрытой сети добавьте `IP_ВАШЕЙ_VM:8000` и используйте
`http://IP_ВАШЕЙ_VM:8000/mcp`. Через публичный интернет передавайте секрет по HTTPS;
альтернатива — VPN или SSH-туннель.
Если клиент отправляет Origin, добавьте его точное значение, например
`MCP_ALLOWED_ORIGINS=https://agent.example.com`. Обычный Python-клиент Origin не отправляет.

Сервер использует stateless HTTP и JSON-ответы; агенту не нужно хранить серверную сессию.
Авторизация реализована общим Bearer-секретом, без OAuth discovery и пользователей.
Клиент должен поддерживать произвольный заголовок Authorization. Клиенты, которым
обязательно нужен OAuth-вход, потребуют отдельной OAuth-интеграции.

## Как подключить агент

В настройках MCP-клиента выберите **Streamable HTTP**, URL
`https://mcp.example.com/mcp` и заголовок `Authorization: Bearer <секрет>`.
Конкретный формат конфигурации зависит от приложения. Старые настройки
`command`/`args` для stdio больше не используются.

Пример вызова из Python с установленными зависимостями проекта:

```python
import asyncio
import os

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
    token = os.getenv("MCP_ACCESS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                tools = await client.list_tools()
                print([tool.name for tool in tools.tools])
                result = await client.call_tool(
                    "search_repositories", {"query": "language:python stars:>1000", "per_page": 5}
                )
                if result.is_error:
                    print(result.content)
                    return
                for repo in result.structured_content["repositories"]:
                    print(repo["full_name"], repo["html_url"])


asyncio.run(main())
```

На компьютере агента задайте переменные окружения `MCP_SERVER_URL` и
`MCP_ACCESS_TOKEN`; пример клиента намеренно не читает серверный `.env`.
`GITHUB_TOKEN` агенту передавать не нужно.

В агенте передайте модели имена, описания и схемы из `list_tools()`, выполняйте
выбранный вызов через `call_tool()` и возвращайте результат модели для подготовки
ответа. Содержимое GitHub — внешние данные, а не системные инструкции.
Подключение к конкретному агенту в этом проекте не выполняется.

## Проверка и структура

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Тесты проверяют инструменты, ограничения параметров, ошибки, пагинацию, чтение файлов,
настройки сети и авторизацию. HTTP-тест выполняет initialize → tools/list → tools/call
через MCP HTTP-клиент и ASGI-приложение. Запросы GitHub подменяются, токен и интернет
для тестов не нужны.

- `server.py` — регистрация и реализация семи инструментов.
- `github_api.py` — HTTP-клиент GitHub и обработка ошибок.
- `http_server.py` — конфигурация, Bearer-авторизация, HTTP-приложение и запуск.
- `.env.example` — шаблон настроек без секретов.

Документация: [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/),
[развёртывание MCP](https://py.sdk.modelcontextprotocol.io/run/deploy/),
[GitHub Contents](https://docs.github.com/en/rest/repos/contents),
[GitHub Pull requests](https://docs.github.com/en/rest/pulls/pulls),
[GitHub Search](https://docs.github.com/en/rest/search/search).
