# Thin wrapper around the Anthropic SDK: structured output + web search,
# with pause_turn handling for server-side tool loops.
import json
import sys
import time
from typing import Type, TypeVar

import httpx

import anthropic
from pydantic import BaseModel
from tavily import TavilyClient

from config import MODEL, TAVILY_API_KEY

T = TypeVar("T", bound=BaseModel)

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY / `ant auth login` profile

_tavily: TavilyClient | None = None


def tavily() -> TavilyClient:
    global _tavily
    if _tavily is None:
        if not TAVILY_API_KEY:
            raise SystemExit("TAVILY_API_KEY not set — required for web search")
        _tavily = TavilyClient(api_key=TAVILY_API_KEY)
    return _tavily


# Client-side (custom) tools — executed locally via Tavily, because the API
# proxy in use expects client-side tool execution, not server-side web tools.
WEB_TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web. Returns top results with title, URL, and a content snippet.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a web page by URL and return its text content (truncated).",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute URL to fetch"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]

FETCH_TRUNCATE = 3000  # chars of page text returned per fetch (cost control)

MAX_CONTINUATIONS = 6

# Transient-error retry (aerolink proxy intermittently returns 503 "no
# healthy upstream" and drops connections mid-stream).
RETRY_WAITS = (5, 15, 30)  # seconds between attempts; 3 retries total


def _stream_with_retry(kwargs: dict):
    """One API call with retry on 5xx / connection-reset only.

    4xx errors, refusals, and everything else raise immediately — those
    are real problems, not transient upstream failures.
    """
    for attempt, wait in enumerate(RETRY_WAITS, start=1):
        try:
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except (anthropic.InternalServerError, httpx.RemoteProtocolError) as e:
            print(f"    [transient {type(e).__name__}: {e} — "
                  f"retrying (attempt {attempt}/{len(RETRY_WAITS)}) after {wait}s...]",
                  file=sys.stderr)
            time.sleep(wait)
    # Final attempt — let whatever happens propagate.
    with client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()

# Cost control: thinking off. (On Sonnet 5, omitting the field runs adaptive
# thinking by default, so it must be disabled explicitly; budget_tokens is
# not supported and returns a 400.)
THINKING = {"type": "disabled"}


class ModelRefusal(RuntimeError):
    """Safety classifiers declined the request (stop_reason: refusal)."""


def describe_error(e: Exception) -> str:
    """Full diagnostic string: type, args, and HTTP details when present."""
    parts = [f"type: {type(e).__module__}.{type(e).__name__}",
             f"message: {e}",
             f"args: {e.args!r}"]
    status = getattr(e, "status_code", None)
    if status is not None:
        parts.append(f"status_code: {status}")
    body = getattr(e, "body", None)
    if body is not None:
        parts.append(f"body: {body!r}")
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            parts.append(f"response.text: {resp.text}")
        except Exception as inner:
            parts.append(f"response.text unavailable: {inner!r}")
    req_id = getattr(e, "request_id", None)
    if req_id:
        parts.append(f"request_id: {req_id}")
    return "\n".join(parts)


# Constraints the structured-outputs API rejects; Pydantic still validates
# them client-side after parsing.
UNSUPPORTED_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                    "multipleOf", "minLength", "maxLength", "minItems", "maxItems")


def _schema_for(model_cls: Type[BaseModel]) -> dict:
    schema = model_cls.model_json_schema()
    # Structured outputs require additionalProperties: false on objects and
    # reject numeric/length constraints.
    def patch(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for key in UNSUPPORTED_KEYS:
                node.pop(key, None)
            for v in node.values():
                patch(v)
        elif isinstance(node, list):
            for v in node:
                patch(v)
    patch(schema)
    return schema


def execute_tool(tool_use_block) -> dict:
    """Run one client-side tool call via Tavily; return a tool_result block."""
    name, args = tool_use_block.name, tool_use_block.input
    try:
        if name == "web_search":
            resp = tavily().search(query=args["query"], max_results=5)
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "content": (r.get("content") or "")[:500]}
                for r in resp.get("results", [])[:5]
            ]
            content = json.dumps(results)
        elif name == "web_fetch":
            resp = tavily().extract(urls=[args["url"]])
            ok = resp.get("results") or []
            if ok:
                content = (ok[0].get("raw_content") or "")[:FETCH_TRUNCATE]
            else:
                failed = (resp.get("failed_results") or [{}])[0]
                raise RuntimeError(failed.get("error") or "fetch failed")
        else:
            raise RuntimeError(f"unknown tool: {name}")
        return {"type": "tool_result", "tool_use_id": tool_use_block.id,
                "content": content}
    except Exception as e:
        import sys
        print(f"    [tool {name} failed]\n{describe_error(e)}", file=sys.stderr)
        return {"type": "tool_result", "tool_use_id": tool_use_block.id,
                "content": f"Error ({type(e).__name__}): {e}", "is_error": True}


def _run_tool_loop(kwargs: dict, prompt: str):
    """Client-side tool loop: execute web_search/web_fetch locally via Tavily."""
    messages = [{"role": "user", "content": prompt}]
    kwargs = {**kwargs, "messages": messages}
    searches = fetches = 0

    def _call():
        # Stream to avoid HTTP timeouts on long research turns.
        return _stream_with_retry(kwargs)

    response = _call()
    iterations = 0
    while response.stop_reason == "tool_use":
        iterations += 1
        if iterations > MAX_CONTINUATIONS:
            # Give the model its results so far but stop looping.
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        results = []
        for tu in tool_uses:
            if tu.name == "web_search":
                searches += 1
            elif tu.name == "web_fetch":
                fetches += 1
            results.append(execute_tool(tu))
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})
        response = _call()

    if response.stop_reason == "refusal":
        raise ModelRefusal("Model refused the request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Output truncated — raise max_tokens for this call")
    print(f"    [web: {searches} searches, {fetches} fetches, "
          f"{iterations} tool rounds]")
    return response


def _run_plain(kwargs: dict, prompt: str):
    """Single call, no tools."""
    kwargs = {**kwargs, "messages": [{"role": "user", "content": prompt}]}
    response = _stream_with_retry(kwargs)
    if response.stop_reason == "refusal":
        raise ModelRefusal("Model refused the request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Output truncated — raise max_tokens for this call")
    return response


def _lenient_json(text: str) -> dict:
    """Extract a JSON object from text that may carry fences or prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def _parse_output(response, output_model: Type[T]) -> T:
    # The model may emit narration text blocks alongside the schema-constrained
    # one — parse the first block that validates.
    last_err: Exception | None = None
    for b in response.content:
        if b.type != "text" or not b.text.strip():
            continue
        try:
            return output_model.model_validate(_lenient_json(b.text))
        except Exception as e:
            last_err = e
    raise RuntimeError(
        "No valid JSON block in response: "
        f"{last_err}; blocks="
        + json.dumps([
            {"type": b.type, "text": b.text[:150]} if b.type == "text" else {"type": b.type}
            for b in response.content
        ])
    )


def research_call(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 8000,
) -> str:
    """Web-tool research turn returning free-form text notes (no schema)."""
    kwargs: dict = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": THINKING,
        "tools": WEB_TOOLS,
    }
    if system:
        kwargs["system"] = system

    response = _run_tool_loop(kwargs, prompt)

    text = "\n".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise RuntimeError("Research call returned no text")
    return text


def structured_call(
    prompt: str,
    output_model: Type[T],
    system: str | None = None,
    use_web: bool = False,
    max_tokens: int = 4000,
) -> T:
    """One agent call: optional web research, schema-validated JSON out.

    The schema is embedded in the prompt (belt) in addition to
    output_config (suspenders) — the output_config constraint alone has
    proven unreliable on multi-block responses in this environment.
    """
    schema = _schema_for(output_model)
    full_prompt = (
        f"{prompt}\n\n"
        "Respond with ONLY a single JSON object matching this JSON schema — "
        "no prose before or after it:\n"
        f"{json.dumps(schema)}"
    )
    kwargs: dict = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": THINKING,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if system:
        kwargs["system"] = system
    if use_web:
        kwargs["tools"] = WEB_TOOLS
        response = _run_tool_loop(kwargs, full_prompt)
    else:
        response = _run_plain(kwargs, full_prompt)

    return _parse_output(response, output_model)
