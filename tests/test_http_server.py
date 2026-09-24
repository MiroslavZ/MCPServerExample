import os
import unittest
from unittest.mock import patch

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from http_server import Settings, create_app
from server import mcp


class SettingsTests(unittest.TestCase):
    def test_environment(self):
        with patch.dict(os.environ, {
            "MCP_HOST": "0.0.0.0", "MCP_PORT": "9000", "MCP_ACCESS_TOKEN": "secret",
            "MCP_ALLOWED_HOSTS": "mcp.example.com, 10.0.0.1:9000",
            "MCP_ALLOWED_ORIGINS": "https://agent.example.com",
        }, clear=True), patch("http_server.load_dotenv"):
            settings = Settings.from_env()
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.allowed_hosts, ["mcp.example.com", "10.0.0.1:9000"])
        self.assertEqual(settings.allowed_origins, ["https://agent.example.com"])
        self.assertNotIn("secret", repr(settings))

    def test_external_access_requires_token(self):
        with self.assertRaises(ValueError):
            Settings(host="0.0.0.0").validate()
        for settings in (Settings(port=0), Settings(port=65536), Settings(allowed_hosts=[])):
            with self.assertRaises(ValueError):
                settings.validate()


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_host_and_origin(self):
        app = create_app(mcp, Settings(access_token="test-mcp-secret"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
                for headers in ({}, {"Authorization": "Bearer wrong"}):
                    result = await client.post("/mcp", json={}, headers=headers)
                    self.assertEqual(result.status_code, 401)
                auth = {"Authorization": "Bearer test-mcp-secret"}
                for extra in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
                    result = await client.post("/mcp", json={}, headers={**auth, **extra})
                    self.assertIn(result.status_code, (400, 403, 421))

    async def test_mcp_over_http(self):
        app = create_app(mcp, Settings(
            access_token="test-mcp-secret", allowed_hosts=["mcp.example.com"],
        ))
        with patch("github_api.get", return_value=({"full_name": "o/r"}, False)) as github:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    headers={"Authorization": "Bearer test-mcp-secret"},
                ) as http:
                    # HTTPS URL и Host, которые приложение использует за reverse proxy.
                    async with streamable_http_client("https://mcp.example.com/mcp", http_client=http) as streams:
                        async with ClientSession(*streams) as client:
                            await client.initialize()
                            tools = (await client.list_tools()).tools
                            self.assertEqual(len(tools), 7)
                            repository_tool = next(tool for tool in tools if tool.name == "get_repository")
                            self.assertEqual(repository_tool.input_schema["required"], ["owner", "repo"])
                            self.assertTrue(repository_tool.annotations.read_only_hint)
                    # Приложение может получить каталог и выполнить вызов в разных сессиях.
                    async with streamable_http_client("https://mcp.example.com/mcp", http_client=http) as streams:
                        async with ClientSession(*streams) as client:
                            await client.initialize()
                            result = await client.call_tool("get_repository", {"owner": "o", "repo": "r"})
                            self.assertFalse(result.is_error)
                            self.assertEqual(result.structured_content, {"full_name": "o/r"})
                            github.assert_awaited_once_with("/repos/o/r")
