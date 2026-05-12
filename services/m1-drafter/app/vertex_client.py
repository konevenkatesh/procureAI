"""R7.4 + R8.6 — Vertex AI clients (Gemini-only after R8.6 user decision).

Model wrappers:
  - gemini_flash() / gemini_flash_async(): BoQ line item generation in batches
  - gemini_pro()   / gemini_pro_async():   PCC clause overrides, Section VI adaptations.
                                            Also acts as Flash-drift fallback (R8.6,
                                            replacing the removed Sonnet path).

Auth:
  - Local dev: gcloud user creds via `gcloud auth print-access-token`
  - Cloud Run: runtime SA (procure-ai-runtime) via metadata server

All clients return structured output via Pydantic schema validation
(response_schema). No API keys — pure REST + token-based auth.

R8.6: Anthropic Sonnet-4-via-Vertex-Model-Garden removed entirely. The user's
project lacked Anthropic publisher access on Vertex Model Garden, so every
fallback attempt returned 404. Replaced with Gemini Pro as the drift fallback —
the gemini-2.5-pro structured-output is robust enough on its own.

Also includes embed_text() / embed_texts_batch() for Vertex AI text-embedding-005 (768-dim).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


# ─── Config ──────────────────────────────────────────────────────────


PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "procureai-prod")
PRIMARY_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")     # broader model availability
ASIA_LOCATION = "asia-south1"                                            # for embeddings (data residency)

FLASH_MODEL_ID = "gemini-2.5-flash"
PRO_MODEL_ID = "gemini-2.5-pro"
EMBEDDING_MODEL_ID = "text-embedding-005"


# ─── Token management ────────────────────────────────────────────────


_token_cache: dict[str, tuple[str, float]] = {}
_TOKEN_TTL_SEC = 50 * 60  # tokens live ~60min; refresh at 50


def _get_token() -> str:
    """Fetch a fresh access token from gcloud OR Cloud Run metadata server."""
    now = time.time()
    cached = _token_cache.get("access_token")
    if cached and (now - cached[1]) < _TOKEN_TTL_SEC:
        return cached[0]

    # Cloud Run: metadata server
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            token = data["access_token"]
            _token_cache["access_token"] = (token, now)
            return token
    except Exception:
        pass

    # Local dev: gcloud
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        token = result.stdout.strip()
        _token_cache["access_token"] = (token, now)
        return token
    except Exception as e:
        raise RuntimeError(
            f"Cannot mint Vertex AI access token via metadata server or gcloud: {e}"
        ) from e


def _vertex_url(location: str, model_id: str, action: str = "generateContent") -> str:
    """Build Vertex AI generateContent URL for a Gemini model."""
    return (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
        f"/locations/{location}/publishers/google/models/{model_id}:{action}"
    )


# ─── HTTP helpers ────────────────────────────────────────────────────


def _post_json(url: str, body: dict, timeout: int = 120) -> dict:
    token = _get_token()
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body_text[:500]}") from e


# ─── Gemini Flash + Pro ──────────────────────────────────────────────


def _pydantic_to_response_schema(model: Type[BaseModel]) -> dict:
    """Vertex AI's responseSchema follows OpenAPI 3.0 subset — NO $ref / $defs.

    Pydantic emits nested `$ref` / `$defs` for any nested BaseModel. We
    walk the schema, inline-dereference every $ref pointer, drop $defs,
    and strip unsupported keywords (anyOf+null → nullable=true, title,
    default, etc.). Recursive types are flattened to one level then
    truncated with `additionalProperties: true` to avoid infinite loops.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", None) or raw.pop("definitions", None) or {}

    def _inline(node, depth=0):
        if depth > 8:        # hard cap on recursion
            return {"type": "object", "additionalProperties": True}
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                # "#/$defs/Foo" → defs["Foo"]
                name = ref.rsplit("/", 1)[-1]
                target = defs.get(name)
                if target is None:
                    return {"type": "object", "additionalProperties": True}
                # Inline + merge any sibling keys (rare but valid)
                merged = _inline(target, depth + 1)
                for k, v in node.items():
                    if k == "$ref":
                        continue
                    merged[k] = _inline(v, depth + 1)
                return merged
            # Convert anyOf with null → nullable
            if "anyOf" in node:
                variants = node["anyOf"]
                non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
                has_null = len(non_null) != len(variants)
                if len(non_null) == 1:
                    inlined = _inline(non_null[0], depth + 1)
                    if has_null:
                        inlined["nullable"] = True
                    return {**{k: _inline(v, depth + 1) for k, v in node.items() if k != "anyOf"}, **inlined}
            cleaned = {}
            for k, v in node.items():
                if k in ("title", "default", "$defs", "definitions"):
                    continue
                cleaned[k] = _inline(v, depth + 1)
            return cleaned
        if isinstance(node, list):
            return [_inline(v, depth + 1) for v in node]
        return node

    return _inline(raw)


def gemini_call(
    *,
    model_id: str,
    prompt: str,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 2048,
    temperature: float = 0.1,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> dict:
    """Generic Gemini call. Returns parsed JSON if response_schema is provided, else text response.

    thinking_budget: tokens reserved for 2.5 chain-of-thought. R7.4 lesson: Flash
    consumed 285/300 tokens on thinking with the default budget. Pass 0 to disable
    thinking entirely (recommended for BoQ batching and short structured output).
    """
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
        },
    }
    if thinking_budget is not None:
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if response_schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = _pydantic_to_response_schema(response_schema)

    url = _vertex_url(location, model_id)
    resp = _post_json(url, body, timeout=180)

    # Extract text/JSON
    try:
        cand = resp["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {resp}") from e

    usage = resp.get("usageMetadata", {})
    out: dict[str, Any] = {
        "text":              text,
        "prompt_tokens":     usage.get("promptTokenCount", 0),
        "completion_tokens": usage.get("candidatesTokenCount", 0),
        "thought_tokens":    usage.get("thoughtsTokenCount", 0),
        "total_tokens":      usage.get("totalTokenCount", 0),
        "model_version":     resp.get("modelVersion", model_id),
        "raw":               resp,
    }

    if response_schema:
        try:
            parsed = json.loads(text)
            out["parsed"] = response_schema.model_validate(parsed)
            out["parse_ok"] = True
        except (json.JSONDecodeError, ValidationError) as e:
            out["parsed"] = None
            out["parse_ok"] = False
            out["parse_error"] = str(e)
    return out


def gemini_flash(
    prompt: str,
    *,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 2048,
    temperature: float = 0.1,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = 0,
) -> dict:
    """Cheap, fast — for BoQ line items + retrieval helpers.
    Defaults thinking_budget=0 (Flash thinking is rarely useful for structured BoQ output)."""
    return gemini_call(
        model_id=FLASH_MODEL_ID,
        prompt=prompt,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        location=location,
        system_instruction=system_instruction,
        thinking_budget=thinking_budget,
    )


def gemini_pro(
    prompt: str,
    *,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 4096,
    temperature: float = 0.2,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> dict:
    """Reasoning — for PCC clause overrides, complex Section VI adaptations.
    Default thinking_budget=None means Pro decides; pass an explicit value to cap."""
    return gemini_call(
        model_id=PRO_MODEL_ID,
        prompt=prompt,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        location=location,
        system_instruction=system_instruction,
        thinking_budget=thinking_budget,
    )


# ─── Async variants (R8.6) — httpx.AsyncClient for parallel batching ──


async def _post_json_async(url: str, body: dict, timeout: int = 180) -> dict:
    """Async sibling of _post_json — used by run_batches_parallel for concurrent
    Flash calls. Reads metadata-server / gcloud token via the same cached path
    (token fetch is not awaited; the sync path is fast enough)."""
    import httpx
    token = _get_token()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            text = resp.text
            raise RuntimeError(f"HTTP {resp.status_code}: {text[:500]}")
        return resp.json()


async def gemini_call_async(
    *,
    model_id: str,
    prompt: str,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 2048,
    temperature: float = 0.1,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> dict:
    """Async sibling of gemini_call. Same shape/contract."""
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
        },
    }
    if thinking_budget is not None:
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if response_schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = _pydantic_to_response_schema(response_schema)

    url = _vertex_url(location, model_id)
    resp = await _post_json_async(url, body, timeout=180)

    try:
        cand = resp["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {resp}") from e

    usage = resp.get("usageMetadata", {})
    out: dict[str, Any] = {
        "text":              text,
        "prompt_tokens":     usage.get("promptTokenCount", 0),
        "completion_tokens": usage.get("candidatesTokenCount", 0),
        "thought_tokens":    usage.get("thoughtsTokenCount", 0),
        "total_tokens":      usage.get("totalTokenCount", 0),
        "model_version":     resp.get("modelVersion", model_id),
        "raw":               resp,
    }

    if response_schema:
        try:
            parsed = json.loads(text)
            out["parsed"] = response_schema.model_validate(parsed)
            out["parse_ok"] = True
        except (json.JSONDecodeError, ValidationError) as e:
            out["parsed"] = None
            out["parse_ok"] = False
            out["parse_error"] = str(e)
    return out


async def gemini_flash_async(
    prompt: str,
    *,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 2048,
    temperature: float = 0.1,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = 0,
) -> dict:
    return await gemini_call_async(
        model_id=FLASH_MODEL_ID,
        prompt=prompt,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        location=location,
        system_instruction=system_instruction,
        thinking_budget=thinking_budget,
    )


async def gemini_pro_async(
    prompt: str,
    *,
    response_schema: Optional[Type[BaseModel]] = None,
    max_output_tokens: int = 4096,
    temperature: float = 0.2,
    location: str = PRIMARY_LOCATION,
    system_instruction: Optional[str] = None,
    thinking_budget: Optional[int] = None,
) -> dict:
    """R8.6 — async Pro is also the Flash-drift fallback (replaces removed Sonnet)."""
    return await gemini_call_async(
        model_id=PRO_MODEL_ID,
        prompt=prompt,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        location=location,
        system_instruction=system_instruction,
        thinking_budget=thinking_budget,
    )


# ─── Embeddings ──────────────────────────────────────────────────────


def embed_text(text: str, *, task_type: str = "RETRIEVAL_DOCUMENT", location: str = PRIMARY_LOCATION) -> list[float]:
    """Vertex AI text-embedding-005 (768 dim)."""
    body = {
        "instances": [{
            "task_type": task_type,
            "content":   text[:20000],  # cap input
        }],
    }
    url = _vertex_url(location, EMBEDDING_MODEL_ID, action="predict")
    resp = _post_json(url, body, timeout=60)
    return resp["predictions"][0]["embeddings"]["values"]


def embed_texts_batch(texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT", location: str = PRIMARY_LOCATION) -> list[list[float]]:
    """Embed multiple texts in a single request (Vertex supports up to 250 per call)."""
    if not texts:
        return []
    body = {
        "instances": [
            {"task_type": task_type, "content": t[:20000]}
            for t in texts
        ],
    }
    url = _vertex_url(location, EMBEDDING_MODEL_ID, action="predict")
    resp = _post_json(url, body, timeout=120)
    return [pred["embeddings"]["values"] for pred in resp["predictions"]]


# ─── Smoke test ──────────────────────────────────────────────────────


if __name__ == "__main__":
    print("Testing Vertex AI Flash...")
    r = gemini_flash("List exactly 3 IS standards relevant to MS pipes. Output: a JSON array of strings.",
                     max_output_tokens=200)
    print(f"  text: {r['text'][:200]}")
    print(f"  tokens: prompt={r['prompt_tokens']} completion={r['completion_tokens']} thought={r['thought_tokens']}")

    print("\nTesting Vertex AI text-embedding-005...")
    emb = embed_text("AHU 4000 CFM double-skin panel construction")
    print(f"  embedding dim: {len(emb)}")
    print(f"  first 5 values: {emb[:5]}")
