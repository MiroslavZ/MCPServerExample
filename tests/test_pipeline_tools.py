import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2 as httpx
import anyio
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import EmbeddedResource
from openai import APIConnectionError, APIStatusError, APITimeoutError

import pipeline_tools
from server import mcp


def completion(content="Сводка", finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content, refusal=refusal),
    )])


class SummarizeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.dict(os.environ, {"LLM_API_KEY": "test-secret"}, clear=True))
        self.dotenv = self.enterContext(patch.object(pipeline_tools, "load_dotenv"))
        self.create = AsyncMock(return_value=completion())
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))
        self.client_context = MagicMock()
        self.client_context.__aenter__ = AsyncMock(return_value=self.client)
        self.client_context.__aexit__ = AsyncMock(return_value=False)
        self.factory = self.enterContext(patch.object(
            pipeline_tools, "AsyncOpenAI", return_value=self.client_context,
        ))

    async def test_data_and_instructions_reach_llm_without_modification(self):
        data = {"commits": [{"sha": "abc", "message": "Правка\nПодробности", "url": "https://example.com"}]}
        self.create.return_value = completion("  Сводка\nСо ссылкой\n")
        result = await pipeline_tools.summarize_data(data, "Укажи SHA и ссылку")
        self.assertEqual(result, {"summary": "  Сводка\nСо ссылкой\n"})
        args = self.create.await_args.kwargs
        self.assertEqual(json.loads(args["messages"][-1]["content"]), data)
        self.assertEqual(args["messages"][-2], {"role": "user", "content": "Укажи SHA и ссылку"})
        self.assertEqual(args["model"], "deepseek-chat")
        self.factory.assert_called_once_with(
            api_key="test-secret", base_url="https://api.deepseek.com", timeout=60, max_retries=0,
        )
        self.client_context.__aexit__.assert_awaited_once()
        self.dotenv.assert_called_once_with(pipeline_tools.SERVER_DIR / ".env")

    async def test_text_and_list_are_accepted(self):
        for data in ("  Факты\r\n\t", [{"title": "Задача"}, "Текст"], {}, []):
            with self.subTest(data=data):
                await pipeline_tools.summarize_data(data, "Сводка")
                sent = self.create.await_args.kwargs["messages"][-1]["content"]
                self.assertEqual(sent if isinstance(data, str) else json.loads(sent), data)

    async def test_environment_and_missing_configuration(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://llm.example/v1", "LLM_MODEL": "custom-model"}):
            await pipeline_tools.summarize_data("data", "summary")
            self.assertEqual(self.factory.call_args.kwargs["base_url"], "https://llm.example/v1")
            self.assertEqual(self.create.await_args.kwargs["model"], "custom-model")
        self.factory.reset_mock()
        for overrides in ({"LLM_API_KEY": " "}, {"LLM_BASE_URL": ""}, {"LLM_MODEL": " "}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                with self.assertRaises(ToolError):
                    await pipeline_tools.summarize_data("data", "summary")
        self.factory.assert_not_called()

    async def test_limits_and_invalid_data_prevent_network_calls(self):
        for data, instructions in (
            ("  ", "summary"), ("x" * 200_001, "summary"),
            ({"data": "x" * 200_000}, "summary"), ("data", " "),
            ("data", "x" * 4001), ({"value": float("nan")}, "summary"),
            ("\ud800", "summary"), ("data", "\ud800"),
        ):
            with self.subTest(length=len(str(data)), instructions_length=len(instructions)):
                with self.assertRaises(ToolError):
                    await pipeline_tools.summarize_data(data, instructions)
        self.factory.assert_not_called()
        await pipeline_tools.summarize_data("x" * 200_000, "summary")
        self.create.assert_awaited_once()

    async def test_total_timeout_closes_client(self):
        async def slow_response(**kwargs):
            await anyio.sleep(1)

        self.create.side_effect = slow_response
        with patch.object(pipeline_tools, "fail_after", side_effect=lambda seconds: anyio.fail_after(0.01)):
            with self.assertRaisesRegex(ToolError, "LLM"):
                await pipeline_tools.summarize_data("data", "summary")
        self.client_context.__aexit__.assert_awaited_once()

    async def test_api_errors_hide_secrets(self):
        request = httpx.Request("POST", "https://llm.example/v1")
        for error in (
            APIConnectionError(message="test-secret", request=request),
            APITimeoutError(request=request),
            APIStatusError("test-secret", response=httpx.Response(401, request=request), body={"token": "test-secret"}),
            ValueError("test-secret"),
        ):
            with self.subTest(error=type(error).__name__):
                self.create.side_effect = error
                async with Client(mcp) as client:
                    result = await client.call_tool("summarize", {"data": "Факты"})
                self.assertTrue(result.is_error)
                self.assertNotIn("test-secret", result.content[0].text)
                self.assertIn("LLM", result.content[0].text)

    async def test_incomplete_empty_refused_and_oversized_responses_are_errors(self):
        for response in (
            SimpleNamespace(choices=[]), SimpleNamespace(choices=None), SimpleNamespace(),
            SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=None)]),
            completion(None), completion("  "),
            completion("Частичная сводка", "length"), completion("Отказ", "content_filter"),
            completion("Отказ", refusal="cannot comply"), completion("x" * 200_001),
        ):
            with self.subTest(response=str(response)[:60]):
                self.create.return_value = response
                with self.assertRaises(ToolError):
                    await pipeline_tools.summarize_data("Факты", "Сводка")


class SaveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"MCP_OUTPUT_DIR": str(self.root)}, clear=True))
        self.enterContext(patch.object(pipeline_tools, "load_dotenv"))

    async def test_exact_file_resource_metadata_and_no_overwrite(self):
        text = "  Сводка\r\nНовая строка\n\tПривет 🌍\n"
        results = []
        async with Client(mcp) as client:
            for _ in range(2):
                results.append(await client.call_tool("save_to_file", {"content": text, "filename": "Моя сводка.txt"}))
        for result in results:
            self.assertFalse(result.is_error)
            metadata = result.structured_content
            self.assertEqual(metadata["filename"], "Моя сводка.txt")
            self.assertEqual(metadata["size_bytes"], len(text.encode("utf-8")))
            self.assertEqual(metadata["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
            self.assertEqual(json.loads(result.content[0].text), metadata)
            resource = next(block.resource for block in result.content if isinstance(block, EmbeddedResource))
            self.assertEqual(resource.text, text)
            self.assertEqual(resource.mime_type, "text/plain")
            self.assertEqual(str(resource.uri), metadata["uri"])
        files = list(self.root.glob("*/Моя сводка.txt"))
        self.assertEqual(len(files), 2)
        self.assertNotEqual(results[0].structured_content["uri"], results[1].structured_content["uri"])
        for file in files:
            self.assertEqual(file.read_bytes(), text.encode("utf-8"))
            self.assertIn(file.as_uri(), [result.structured_content["uri"] for result in results])

    async def test_unsafe_filenames_are_rejected_without_writes(self):
        async with Client(mcp) as client:
            for filename in (
                "../escape.txt", "sub/file.txt", r"sub\file.txt", "/tmp/file.txt", r"C:\file.txt",
                "CON.txt", "nul.txt", "COM1.txt", "COM¹.txt", "lpt9.txt", "name:stream.txt", ".hidden.txt",
                "x\x00.txt", "x\n.txt", "?name.txt", "x.txt ", " x.txt", "x.csv", "x" * 100 + ".txt",
            ):
                with self.subTest(filename=filename):
                    result = await client.call_tool("save_to_file", {"content": "data", "filename": filename})
                    self.assertTrue(result.is_error)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_empty_text_size_limit_and_default_filename(self):
        async with Client(mcp) as client:
            result = await client.call_tool("save_to_file", {"content": "x" * 200_001})
            self.assertTrue(result.is_error)
            self.assertEqual(list(self.root.iterdir()), [])
            for content in ("", "ю" * 200_000):
                result = await client.call_tool("save_to_file", {"content": content})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["filename"], "summary.txt")
                self.assertEqual(result.structured_content["size_bytes"], len(content.encode("utf-8")))

    async def test_relative_output_directory_is_relative_to_server(self):
        with patch.object(pipeline_tools, "SERVER_DIR", self.root), patch.dict(os.environ, {"MCP_OUTPUT_DIR": "relative"}):
            pipeline_tools.save_text_file("data", "result.txt")
        self.assertEqual(len(list((self.root / "relative").glob("*/result.txt"))), 1)

    async def test_write_error_and_empty_output_setting(self):
        with patch.object(Path, "open", side_effect=OSError("test-secret")):
            with self.assertRaisesRegex(ToolError, "Не удалось сохранить") as raised:
                pipeline_tools.save_text_file("data", "summary.txt")
            self.assertNotIn("test-secret", str(raised.exception))
        self.assertEqual(list(self.root.iterdir()), [])
        with patch.dict(os.environ, {"MCP_OUTPUT_DIR": " "}):
            with self.assertRaises(ToolError):
                pipeline_tools.save_text_file("data", "summary.txt")


if __name__ == "__main__":
    unittest.main()
