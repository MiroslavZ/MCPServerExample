import base64
import os
import unittest
from unittest.mock import patch

import httpx2 as httpx
from mcp import Client

import github_api
from server import mcp


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.status = 200
        self.payload = {"full_name": "octocat/Hello-World", "stargazers_count": 42}
        self.headers = {}
        self.network_error = False
        real_client = httpx.AsyncClient

        def respond(request):
            self.requests.append(request)
            if self.network_error:
                raise httpx.ConnectError("network error", request=request)
            return httpx.Response(self.status, json=self.payload, headers=self.headers)

        self.enterContext(patch.dict(os.environ, {"GITHUB_TOKEN": "test-secret"}))
        self.enterContext(patch.object(github_api, "load_dotenv"))
        self.enterContext(patch.object(
            github_api.httpx, "AsyncClient",
            side_effect=lambda **kwargs: real_client(
                **kwargs, transport=httpx.MockTransport(respond),
            ),
        ))

    async def call_tool(self, name, arguments):
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments)

    async def test_registration_and_schema(self):
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        self.assertEqual(set(tools), {
            "get_repository", "list_issues", "get_issue", "search_repositories",
            "list_pull_requests", "get_pull_request", "get_repository_content",
        })
        schema = tools["list_issues"].input_schema
        self.assertEqual(schema["required"], ["owner", "repo"])
        self.assertEqual(schema["properties"]["per_page"]["maximum"], 100)
        self.assertTrue(schema["properties"]["owner"]["description"])

    async def test_repository_result_and_auth(self):
        result = await self.call_tool("get_repository", {"owner": "octocat", "repo": "Hello-World"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, self.payload)
        self.assertEqual(self.requests[0].url.path, "/repos/octocat/Hello-World")
        self.assertEqual(self.requests[0].headers["Authorization"], "Bearer test-secret")

    async def test_repository_keeps_useful_fields_without_service_metadata(self):
        self.payload = {
            "full_name": "o/r", "description": "Example", "html_url": "https://github.com/o/r",
            "topics": ["mcp"], "stargazers_count": 42, "default_branch": "main",
            "owner": {"login": "o", "html_url": "https://github.com/o", "followers_url": "unused"},
            "license": {"name": "MIT License", "spdx_id": "MIT", "node_id": "unused"},
            "permissions": {"admin": True}, "events_url": "unused", "node_id": "unused",
        }
        result = await self.call_tool("get_repository", {"owner": "o", "repo": "r"})
        self.assertEqual(result.structured_content, {
            "full_name": "o/r", "description": "Example", "html_url": "https://github.com/o/r",
            "topics": ["mcp"], "stargazers_count": 42, "default_branch": "main",
            "owner": {"login": "o", "html_url": "https://github.com/o"},
            "license": {"name": "MIT License", "spdx_id": "MIT"},
        })

    async def test_without_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            await self.call_tool("get_repository", {"owner": "o", "repo": "r"})
        self.assertNotIn("Authorization", self.requests[0].headers)

    async def test_pagination_filters_pull_requests(self):
        self.payload = [{"number": 1}, {"number": 2, "pull_request": {}}]
        self.headers = {"Link": '<https://api.github.com/repos/o/r/issues?page=3>; rel="next"'}
        result = await self.call_tool("list_issues", {
            "owner": "o", "repo": "r", "state": "closed", "page": 2, "per_page": 2,
        })
        self.assertEqual(result.structured_content, {"issues": [{"number": 1}], "page": 2, "next_page": 3})
        self.assertEqual(self.requests[0].url.params["state"], "closed")
        self.assertEqual(self.requests[0].url.params["page"], "2")

    async def test_get_issue_and_reject_pull_request(self):
        self.payload = {"number": 3, "title": "Bug", "body": "Details"}
        result = await self.call_tool("get_issue", {"owner": "o", "repo": "r", "issue_number": 3})
        self.assertEqual(result.structured_content, self.payload)
        self.assertEqual(self.requests[0].url.path, "/repos/o/r/issues/3")
        self.payload["pull_request"] = {}
        result = await self.call_tool("get_issue", {"owner": "o", "repo": "r", "issue_number": 3})
        self.assertTrue(result.is_error)

    async def test_issue_summary_then_details_preserve_full_body(self):
        issue = {
            "number": 3, "title": "Bug", "body": "Подробное описание " * 1000,
            "user": {"login": "octocat", "avatar_url": "unused"},
            "labels": [{"name": "bug", "description": "Something is wrong", "id": 42}],
            "assignees": [{"login": "maintainer", "node_id": "unused"}],
            "milestone": None, "repository_url": "unused",
        }
        self.payload = [issue]
        summary = await self.call_tool("list_issues", {"owner": "o", "repo": "r"})
        summary_issue = summary.structured_content["issues"][0]
        self.assertNotIn("body", summary_issue)
        self.assertEqual(summary_issue["user"], {"login": "octocat"})
        self.assertEqual(summary_issue["labels"], [{"name": "bug", "description": "Something is wrong"}])
        self.assertEqual(summary_issue["assignees"], [{"login": "maintainer"}])
        self.assertNotIn("repository_url", summary_issue)
        self.payload = issue
        details = await self.call_tool("get_issue", {"owner": "o", "repo": "r", "issue_number": 3})
        self.assertEqual(details.structured_content, {**summary_issue, "body": issue["body"]})

    async def test_invalid_arguments_do_not_reach_github(self):
        for extra in ({"per_page": 101}, {"page": 0}, {"state": "invalid"}, {"owner": "../x"}):
            result = await self.call_tool("list_issues", {"owner": "o", "repo": "r", **extra})
            self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])

    async def test_api_errors_are_mcp_errors_without_token(self):
        for status in (401, 403, 404, 429, 500):
            self.status = status
            self.payload = {"message": "test-secret"}
            result = await self.call_tool("get_repository", {"owner": "o", "repo": "r"})
            self.assertTrue(result.is_error)
            self.assertIn(str(status), result.content[0].text)
            self.assertNotIn("test-secret", result.content[0].text)

    async def test_network_error(self):
        self.network_error = True
        result = await self.call_tool("get_repository", {"owner": "o", "repo": "r"})
        self.assertTrue(result.is_error)
        self.assertIn("GitHub", result.content[0].text)

    async def test_search_and_limit(self):
        self.payload = {"items": [{"full_name": "o/r"}], "total_count": 1001, "incomplete_results": False}
        self.headers = {"Link": '<https://api.github.com/search/repositories?page=2>; rel="next"'}
        result = await self.call_tool("search_repositories", {"query": "language:python stars:>100"})
        self.assertEqual(result.structured_content["repositories"], self.payload["items"])
        self.assertEqual(result.structured_content["next_page"], 2)
        self.assertEqual(self.requests[0].url.params["q"], "language:python stars:>100")
        result = await self.call_tool("search_repositories", {"query": "python", "page": 50})
        self.assertIsNone(result.structured_content["next_page"])
        count = len(self.requests)
        for params in ({"query": " "}, {"query": "python", "page": 51}):
            self.assertTrue((await self.call_tool("search_repositories", params)).is_error)
        self.assertEqual(len(self.requests), count)

    async def test_pull_requests(self):
        self.payload = [{"number": 5, "state": "open"}]
        result = await self.call_tool("list_pull_requests", {"owner": "o", "repo": "r"})
        self.assertEqual(result.structured_content["pull_requests"], self.payload)
        self.assertIsNone(result.structured_content["next_page"])
        self.assertEqual(self.requests[-1].url.path, "/repos/o/r/pulls")
        self.payload = {"number": 5, "merged": True}
        result = await self.call_tool("get_pull_request", {"owner": "o", "repo": "r", "pull_number": 5})
        self.assertEqual(result.structured_content, self.payload)
        self.assertEqual(self.requests[-1].url.path, "/repos/o/r/pulls/5")

    async def test_pull_request_keeps_branches_and_statistics_without_nested_repos(self):
        pull = {
            "number": 5, "title": "Fix", "body": "Complete description", "merged": True,
            "head": {"label": "o:fix", "ref": "fix", "sha": "abc", "repo": {"huge": "unused"}},
            "base": {"ref": "main", "sha": "def", "repo": {"huge": "unused"}},
            "commits": 2, "additions": 10, "deletions": 4, "changed_files": 1,
            "_links": {"self": "unused"},
        }
        self.payload = [pull]
        summary = await self.call_tool("list_pull_requests", {"owner": "o", "repo": "r"})
        item = summary.structured_content["pull_requests"][0]
        self.assertEqual(item["head"], {"label": "o:fix", "ref": "fix", "sha": "abc"})
        self.assertEqual(item["base"], {"ref": "main", "sha": "def"})
        self.assertNotIn("body", item)
        self.assertNotIn("_links", item)
        self.payload = pull
        details = await self.call_tool("get_pull_request", {"owner": "o", "repo": "r", "pull_number": 5})
        self.assertEqual(details.structured_content, {**item, "body": pull["body"]})
        self.assertEqual(details.structured_content["changed_files"], 1)

    async def test_content_text_binary_and_directory(self):
        self.payload = {"type": "file", "encoding": "base64", "size": 12,
                        "content": base64.b64encode("Привет".encode()).decode()}
        result = await self.call_tool("get_repository_content", {
            "owner": "o", "repo": ".github", "path": "docs/a #?.txt", "ref": "feature/docs",
        })
        self.assertEqual(result.structured_content["content"], "Привет")
        self.assertEqual(result.structured_content["encoding"], "utf-8")
        self.assertEqual(self.requests[-1].url.path, "/repos/o/.github/contents/docs/a #?.txt")
        self.assertEqual(dict(self.requests[-1].url.params), {"ref": "feature/docs"})
        self.payload["content"] = base64.b64encode(b"\xff\x00").decode()
        result = await self.call_tool("get_repository_content", {"owner": "o", "repo": "r", "path": "a.bin"})
        self.assertEqual(result.structured_content["encoding"], "base64")
        self.payload = [{"type": "dir", "path": "src", "_links": {"git": "unused"}}]
        result = await self.call_tool("get_repository_content", {"owner": "o", "repo": "r"})
        self.assertEqual(result.structured_content["entries"], [{"type": "dir", "path": "src"}])
        self.assertFalse(result.structured_content["limit_reached"])

    async def test_content_errors(self):
        for path in ("../secret", "/etc/passwd", "docs/../a", "docs\\a", "a//b"):
            result = await self.call_tool("get_repository_content", {"owner": "o", "repo": "r", "path": path})
            self.assertTrue(result.is_error)
        self.assertEqual(self.requests, [])
        for payload in (
            {"type": "file", "size": 1_000_001, "encoding": "none"},
            {"type": "file", "size": 10, "encoding": "base64", "content": "%%%"},
        ):
            self.payload = payload
            result = await self.call_tool("get_repository_content", {"owner": "o", "repo": "r", "path": "a"})
            self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
