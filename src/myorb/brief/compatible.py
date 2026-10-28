"""AI services with an OpenAI-compatible API (OpenAI, Gemini, Mistral, Ollama, LM Studio), in
place of Claude.

myOrb talks to Claude through the Anthropic SDK. This client answers the few calls myOrb makes
(`messages.create`, `beta.messages.create`, `beta.messages.stream`) in the same shape, so the
brief, Ask and the email features work unchanged: Anthropic's messages and tools go out as the
Chat Completions API wants them, and its answers come back as Anthropic's content blocks. What
only Claude has is left out: the model chosen for Claude (the user's own model is used), effort,
prompt caching (warming up is a no-op) and the price of the tokens.
"""

import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx2

from myorb.brief.writer import BriefError


@dataclass(frozen=True)
class Service:
    name: str
    base_url: str  # the usual address, changeable (e.g. Ollama on another machine)
    key: bool  # whether it needs an API key
    keys_url: str = ""  # where to create one


ANTHROPIC = "anthropic"
SERVICES = {
    "openai": Service(
        "OpenAI", "https://api.openai.com/v1", True, "https://platform.openai.com/api-keys"
    ),
    "gemini": Service(
        "Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        True,
        "https://aistudio.google.com/apikey",
    ),
    "mistral": Service(
        "Mistral", "https://api.mistral.ai/v1", True, "https://console.mistral.ai/api-keys"
    ),
    "ollama": Service("Ollama", "http://localhost:11434/v1", False),
    "lmstudio": Service("LM Studio", "http://localhost:1234/v1", False),
    "compatible": Service("OpenAI-compatible", "", False),
}


@dataclass
class Block:
    """A content block as the Anthropic SDK has it: text, or a tool call."""

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def model_dump(self, mode: str = "python", exclude_none: bool = False) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Message:
    content: list[Block]
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)


STOP_REASONS = {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(
        str(block.get("text", "")) for block in content or [] if block.get("type") == "text"
    )


def chat_messages(system: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic's system prompt and messages as Chat Completions messages: tool calls on the
    assistant's turn, each tool's result a message of its own after it."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": _text(system)})
    for message in messages:
        content = message["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if message["role"] == "assistant":
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            turn: dict[str, Any] = {"role": "assistant", "content": _text(blocks) or None}
            if calls:
                turn["tool_calls"] = calls
            out.append(turn)
            continue
        for block in blocks:
            if block.get("type") == "tool_result":
                result = _text(block.get("content", ""))
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": f"Error: {result}" if block.get("is_error") else result,
                    }
                )
        if text := _text(blocks):
            out.append({"role": "user", "content": text})
    return out


def chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def _call(tool_id: str | None, name: str, arguments: str) -> Block:
    try:
        data = json.loads(arguments) if arguments.strip() else {}
    except ValueError:
        data = {}
    # Some services (e.g. Gemini) leave the id out; the answer to the call needs one.
    return Block(
        "tool_use",
        id=tool_id or f"call_{secrets.token_hex(6)}",
        name=name,
        input=data if isinstance(data, dict) else {},
    )


def _message(model: str, text: str, calls: list[Block], finish: str | None, usage: Any) -> Message:
    blocks = ([Block("text", text=text)] if text else []) + calls
    stop = "tool_use" if calls else STOP_REASONS.get(finish or "", "end_turn")
    counted = Usage(
        (usage or {}).get("prompt_tokens") or 0, (usage or {}).get("completion_tokens") or 0
    )
    return Message(blocks, stop, model, counted)


def error_text(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict):
        return ""
    error = body.get("error", body)
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error)


class CompatibleClient:
    """An OpenAI-compatible service, answering as the Anthropic SDK's client would."""

    def __init__(
        self, service: str, base_url: str, api_key: str, model: str, http: httpx2.AsyncClient
    ) -> None:
        self.service = SERVICES.get(service, SERVICES["compatible"])
        self.base_url = (base_url or self.service.base_url).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.http = http
        messages = SimpleNamespace(create=self.create, stream=self.stream)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _fail(self, response: httpx2.Response, detail: str) -> BriefError:
        name = self.service.name
        if response.status_code in (401, 403):
            return BriefError(f"{name} rejected the API key. Check it in Integrations.")
        if response.status_code == 429:
            return BriefError(f"{name} is rate limited right now. Try again later.")
        if response.status_code == 404 and "model" in detail.lower():
            return BriefError(f"{name} does not have the model {self.model}: {detail}")
        message = f"{name} returned an error (HTTP {response.status_code})"
        return BriefError(f"{message}: {detail}" if detail else f"{message}.")

    def _unreachable(self) -> BriefError:
        return BriefError(f"{self.service.name} could not be reached at {self.base_url}.")

    def _body(self, kwargs: dict[str, Any], plain_json: bool = False) -> dict[str, Any]:
        system = kwargs.get("system")
        schema = (kwargs.get("output_config") or {}).get("format", {}).get("schema")
        if schema and plain_json:
            # A service without JSON schemas: asked for JSON, the schema in the prompt.
            rule = f"Reply with JSON only, following this schema:\n{json.dumps(schema)}"
            system = f"{_text(system)}\n\n{rule}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages(system, kwargs.get("messages", [])),
        }
        if schema:
            body["response_format"] = (
                {"type": "json_object"}
                if plain_json
                else {
                    "type": "json_schema",
                    "json_schema": {"name": "answer", "schema": schema, "strict": True},
                }
            )
        if kwargs.get("tools"):
            body["tools"] = chat_tools(kwargs["tools"])
        return body

    async def _post(self, body: dict[str, Any]) -> httpx2.Response:
        try:
            return await self.http.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers
            )
        except httpx2.HTTPError as error:
            raise self._unreachable() from error

    async def create(self, **kwargs: Any) -> Message:
        if kwargs.get("max_tokens") == 0:
            # Warming up Claude's cache: nothing to do here.
            return Message([], "end_turn", self.model)
        response = await self._post(self._body(kwargs))
        wants_json = bool((kwargs.get("output_config") or {}).get("format"))
        if response.status_code == 400 and wants_json:
            response = await self._post(self._body(kwargs, plain_json=True))
        if response.status_code >= 400:
            raise self._fail(response, error_text(response))
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        reply = choice.get("message") or {}
        calls = [
            _call(c.get("id"), c["function"]["name"], c["function"].get("arguments") or "")
            for c in reply.get("tool_calls") or []
        ]
        return _message(
            data.get("model") or self.model,
            reply.get("content") or "",
            calls,
            choice.get("finish_reason"),
            data.get("usage"),
        )

    def stream(self, **kwargs: Any) -> "Stream":
        return Stream(self, {**self._body(kwargs), "stream": True})

    async def models(self) -> list[str]:
        """The models the service offers; raises BriefError when it cannot be read."""
        try:
            response = await self.http.get(f"{self.base_url}/models", headers=self._headers)
        except httpx2.HTTPError as error:
            raise self._unreachable() from error
        if response.status_code >= 400:
            raise self._fail(response, error_text(response))
        try:
            listed = response.json().get("data") or []
        except (ValueError, AttributeError):
            listed = []
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in listed if m.get("id"))


class Stream:
    """`async with client.beta.messages.stream(...)`: the answer's text as it comes
    (`text_stream`), then the whole message (`get_final_message`)."""

    def __init__(self, client: CompatibleClient, body: dict[str, Any]) -> None:
        self.client = client
        self.body = body
        self._context: Any = None
        self._response: httpx2.Response | None = None
        self._final: Message | None = None
        self._text = ""
        self._calls: dict[int, dict[str, str]] = {}
        self._finish: str | None = None
        self._usage: Any = None
        self._model = client.model

    async def __aenter__(self) -> "Stream":
        client = self.client
        try:
            url = f"{client.base_url}/chat/completions"
            self._context = client.http.stream("POST", url, json=self.body, headers=client._headers)
            self._response = await self._context.__aenter__()
        except httpx2.HTTPError as error:
            raise client._unreachable() from error
        if self._response.status_code >= 400:
            await self._response.aread()
            detail = error_text(self._response)
            await self._context.__aexit__(None, None, None)
            raise client._fail(self._response, detail)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._context.__aexit__(*exc)

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._read()

    async def _read(self) -> AsyncIterator[str]:
        if self._final is not None:
            return
        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                self._model = chunk.get("model") or self._model
                self._usage = chunk.get("usage") or self._usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    self._finish = choice.get("finish_reason") or self._finish
                    for call in delta.get("tool_calls") or []:
                        kept = self._calls.setdefault(
                            call.get("index", len(self._calls)), {"id": "", "name": "", "args": ""}
                        )
                        kept["id"] = call.get("id") or kept["id"]
                        function = call.get("function") or {}
                        kept["name"] += function.get("name") or ""
                        kept["args"] += function.get("arguments") or ""
                    if piece := delta.get("content"):
                        self._text += piece
                        yield piece
        except httpx2.HTTPError as error:
            raise self.client._unreachable() from error
        calls = [_call(c["id"], c["name"], c["args"]) for _, c in sorted(self._calls.items())]
        self._final = _message(self._model, self._text, calls, self._finish, self._usage)

    async def get_final_message(self) -> Message:
        async for _ in self._read():
            pass
        assert self._final is not None
        return self._final
