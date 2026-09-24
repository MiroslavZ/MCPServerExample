# GitHub MCP Server

Учебный MCP-сервер на Python 3.11+ и `mcp==2.2.0`. Предоставляет семь инструментов
для чтения GitHub API через **Streamable HTTP**. Endpoint: `/mcp`.
Агент может работать на другом компьютере; сервер запускается отдельно.

## Инструменты

| Инструмент | Параметры | Результат |
| --- | --- | --- |
| `get_repository` | `owner`, `repo` | Данные репозитория: описание, URL, язык, звёзды, лицензия и основная ветка |
| `search_repositories` | `query`, `page=1`, `per_page=20` | `repositories`, `total_count`, `incomplete_results`, `page`, `next_page` |
| `list_issues` | `owner`, `repo`, `state="open"`, `page=1`, `per_page=20` | Краткие `issues` без body, `page`, `next_page` |
| `get_issue` | `owner`, `repo`, `issue_number` | Описание, автор, метки и состояние issue |
| `list_pull_requests` | `owner`, `repo`, `state="open"`, `page=1`, `per_page=20` | Краткие `pull_requests` без body, `page`, `next_page` |
| `get_pull_request` | `owner`, `repo`, `pull_number` | Описание, ветки, состояние слияния и статистика PR |
| `get_repository_content` | `owner`, `repo`, `path=""`, `ref=null` | Содержимое файла либо каталог с `entries` и `limit_reached` |

`owner` — пользователь/организация, `repo` — имя репозитория без владельца.
`state`: `open`, `closed`, `all`; номера и `page` ≥ 1; `per_page`: 1–100.
`query` поддерживает синтаксис GitHub Search, например `language:python stars:>100`.
`ref` — ветка, тег или SHA коммита; без него используется основная ветка.
Типы, описания и ограничения параметров доступны через MCP `tools/list`.
Инструменты зарегистрированы декораторами `@mcp.tool()` и возвращают структурированный JSON.
Ответы содержат полезные поля GitHub и ссылки `html_url`, без служебных URL,
аватаров и вложенных копий репозиториев. Автор представлен объектом с `login`
и `html_url`, метки — объектами с `name` и `description`, ветки PR — `label`, `ref`, `sha`.
Поля, отсутствующие в GitHub, не добавляются. Полные описания issues/PR доступны
через `get_issue` / `get_pull_request`, без обрезки; списки не загружают их в контекст LLM.

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

Команды этого раздела выполняются из каталога `MCPServerExample`.
Из корня общего проекта сначала выполните `cd MCPServerExample`.

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
MCP_HOST=127.0.0.1
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

Для VPS с proxy на той же машине используйте показанный `MCP_HOST=127.0.0.1`:
сам MCP слушает только loopback, а внешний доступ проходит через HTTPS и Bearer.
Если proxy в другом контейнере/на другом хосте, можно задать `MCP_HOST=0.0.0.0`
и разрешить вход на 8000 только от proxy. При публикации через proxy токен нужен
даже при loopback bind: проверка настроек не может определить наличие proxy.

Пример развёртывания на Linux VPS:

1. Скопируйте каталог сервера в `/opt/github-mcp`, установите зависимости в
   `/opt/github-mcp/venv` по инструкции выше. Создайте отдельного системного
   пользователя `github-mcp`, дайте ему чтение кода и `.env`; для `.env`
   установите владельца `github-mcp` и права `600`.
2. Укажите настройки из примера выше в `/opt/github-mcp/.env`.
3. Создайте `/etc/systemd/system/github-mcp.service`:

```ini
[Unit]
Description=GitHub MCP server
After=network-online.target
Wants=network-online.target

[Service]
User=github-mcp
Group=github-mcp
WorkingDirectory=/opt/github-mcp
ExecStart=/opt/github-mcp/venv/bin/python /opt/github-mcp/server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Запустите службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now github-mcp
sudo systemctl status github-mcp
```

4. Установите Caddy по [официальной инструкции](https://caddyserver.com/docs/install).
   Направьте DNS `mcp.example.com` на IP VPS. Разрешите входящие TCP 80 и 443
   в firewall VPS и правилах облака. Порт 8000 наружу не открывайте.
   Добавьте в `/etc/caddy/Caddyfile`, подставив свой домен:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy получает HTTPS-сертификат автоматически при корректном DNS и доступных
портах. Для этого HTTP upstream заголовки `Host` и `Authorization` сохраняются;
переписывать `/mcp` не требуется. См. [настройку reverse proxy](https://caddyserver.com/docs/quick-starts/reverse-proxy).
После замены домена также обновите `MCP_ALLOWED_HOSTS` и перезапустите службу:
`sudo systemctl restart github-mcp`.

`MCP_ALLOWED_HOSTS` содержит значения HTTP Host без схемы и пути, через запятую.
Приложение LLMAgentExample допускает HTTP только для loopback-адресов;
удалённый URL должен начинаться с HTTPS. Для разработки без домена можно
пробросить порт через SSH (`ssh -N -L 8000:127.0.0.1:8000 user@VPS`) и подключаться
к `http://127.0.0.1:8000/mcp`; токен сервера при этом по-прежнему передаётся.
Если клиент отправляет Origin, добавьте его точное значение, например
`MCP_ALLOWED_ORIGINS=https://agent.example.com`. Обычный Python-клиент Origin не отправляет.

Сервер использует stateless HTTP и JSON-ответы; агенту не нужно хранить серверную сессию.
Авторизация реализована общим Bearer-секретом, без OAuth discovery и пользователей.
Клиент должен поддерживать произвольный заголовок Authorization. Клиенты, которым
обязательно нужен OAuth-вход, потребуют отдельной OAuth-интеграции.

## Как подключить агент

Сервер запускается отдельно от агента — локально или на VPS. Сначала можно
проверить весь сценарий на одном компьютере, затем заменить URL на HTTPS.

В `.env` **в корне LLMAgentExample** добавьте `MCP_ACCESS_TOKEN` с тем же значением,
что у сервера. Для локального сервера без авторизации этот шаг не нужен.
`GITHUB_TOKEN` хранится только в `MCPServerExample/.env` на машине сервера.
После изменения корневого `.env` перезапустите агент.

Из корня проекта запустите веб-приложение:

```powershell
.\venv\Scripts\python.exe -m llm_agent.web
```

Откройте `http://127.0.0.1:8080`, затем **MCP → Добавить MCP**:

| Поле | Локальная проверка | VPS |
| --- | --- | --- |
| Название | `Мой GitHub MCP` | `Мой GitHub MCP` |
| URL MCP-сервера | `http://127.0.0.1:8000/mcp` | `https://mcp.example.com/mcp` |
| Переменная окружения с токеном | Пусто без авторизации, иначе `MCP_ACCESS_TOKEN` | `MCP_ACCESS_TOKEN` |
| Использовать в чате | Включить | Включить |

В поле токена вводится **имя переменной**, а не секрет. Нажмите **Сохранить MCP**,
затем **Get tools**: каталог должен показать семь инструментов. Закройте панель
и отправьте в чат, например:

> Через мой GitHub MCP получи сведения о репозитории octocat/Hello-World.
> Укажи основную ветку, число звёзд и ссылку на репозиторий.

Агент передаёт модели каталог инструментов и схемы параметров. Модель выбирает
`get_repository` и аргументы `{"owner": "octocat", "repo": "Hello-World"}`;
приложение отправляет MCP `tools/call`, возвращает результат модели, после чего
она готовит ответ. В чате можно раскрыть вызовы и проверить аргументы и результат.
Число звёзд берётся из ответа GitHub, а не из заранее заданного примера.

Другие запросы для проверки:

- «Покажи три последних открытых issue в microsoft/vscode, их номера и ссылки».
- «Прочитай README.md из octocat/Hello-World и кратко объясни содержимое».
- «Найди пять популярных репозиториев по запросу language:python stars:>10000».

Настройки подключения общие для диалогов и хранятся в
`data/conversations/mcp.sqlite3` на стороне приложения. Для удалённого сервера
агенту требуется только URL и `MCP_ACCESS_TOKEN`. Подробнее: [MCP приложения](../MCP.md).

Для прямой проверки из терминала (из корня проекта):

```powershell
.\venv\Scripts\python.exe -m llm_agent --mcp-url http://127.0.0.1:8000/mcp --user "Получить сведения о репозитории octocat/Hello-World через MCP"
```

При авторизации добавьте `--mcp-token-env MCP_ACCESS_TOKEN`; для VPS также
замените URL на `https://mcp.example.com/mcp`.

### Самостоятельный Python-клиент

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

Этот короткий пример проверяет сам протокол без LLM. Полный выбор инструмента
моделью и использование результата реализованы в приложении выше.
Содержимое GitHub — внешние данные, а не системные инструкции.

### Если подключение не работает

- HTTP 401: проверьте совпадение `MCP_ACCESS_TOKEN` и имя переменной в форме,
  перезапустите процессы после изменения `.env`.
- HTTP 421: добавьте домен запроса в `MCP_ALLOWED_HOSTS` сервера без схемы и пути.
- HTTP 403 по Origin: Python-агент не посылает Origin; если его добавляет другой
  клиент, внесите точное значение в `MCP_ALLOWED_ORIGINS`.
- Ошибка GitHub внутри результата инструмента: проверьте `GITHUB_TOKEN` на VPS,
  права на репозиторий, корректность `owner` / `repo` и лимит GitHub API.
- Каталог есть, но инструмент недоступен в чате: включите **Использовать в чате**.
- Логи процесса на VPS: `sudo journalctl -u github-mcp -n 50 --no-pager`.

## Проверка и структура

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Тесты проверяют инструменты, ограничения параметров, ошибки, пагинацию, чтение файлов,
компактные ответы без потери описаний в подробном запросе, настройки сети и авторизацию.
HTTP-тест выполняет initialize → tools/list → tools/call
через MCP HTTP-клиент и ASGI-приложение. Запросы GitHub подменяются, токен и интернет
для тестов не нужны.

- `server.py` — регистрация и реализация семи инструментов.
- `github_api.py` — HTTP-клиент GitHub и обработка ошибок.
- `github_results.py` — отбор полезных полей GitHub для контекста модели.
- `http_server.py` — конфигурация, Bearer-авторизация, HTTP-приложение и запуск.
- `.env.example` — шаблон настроек без секретов.

Документация: [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/),
[развёртывание MCP](https://py.sdk.modelcontextprotocol.io/run/deploy/),
[GitHub Contents](https://docs.github.com/en/rest/repos/contents),
[GitHub Pull requests](https://docs.github.com/en/rest/pulls/pulls),
[GitHub Search](https://docs.github.com/en/rest/search/search).
