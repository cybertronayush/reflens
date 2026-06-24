"""Optional LLM enrichment — per-module prose summaries (intent / patterns).

Off by default. This is the only path to "reason as if direct access" with the
detail *pre-loaded* rather than retrieved: an LLM reads each module's Tier-1
outline and writes a few sentences on its responsibility and notable patterns,
stored in the index and surfaced in the digest.

Provider-agnostic: any OpenAI-compatible /chat/completions endpoint via stdlib
urllib (no SDK). Credentials come from env or CLI flags; nothing is hardcoded.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

from .engine import Repo

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

_SYSTEM = (
    "You summarize a software module for an engineer who will apply its patterns "
    "elsewhere. Given the module's file/symbol outline, write 3-5 sentences covering: "
    "its responsibility, key components, and notable conventions/patterns. Be concrete "
    "and specific. No preamble, no bullet lists."
)


class EnrichmentError(RuntimeError):
    pass


def resolve_credentials(
    api_key: Optional[str], base_url: Optional[str], model: Optional[str]
) -> tuple[str, str, str]:
    key = (api_key or os.environ.get("REFLENS_LLM_API_KEY")
           or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise EnrichmentError(
            "no API key. Pass --api-key or set REFLENS_LLM_API_KEY / OPENAI_API_KEY."
        )
    url = (base_url or os.environ.get("REFLENS_LLM_BASE_URL")
           or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    mdl = (model or os.environ.get("REFLENS_LLM_MODEL") or DEFAULT_MODEL)
    return key, url, mdl


def _chat(base_url: str, api_key: str, model: str, system: str, user: str, timeout: int = 90) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise EnrichmentError(f"LLM HTTP {e.code}: {e.read()[:200].decode('utf-8','replace')}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise EnrichmentError(f"LLM request failed: {e}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        raise EnrichmentError(f"unexpected LLM response shape: {str(data)[:200]}")


def enrich_repo(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    max_modules: int = 25,
    ctx_budget: int = 6000,
    progress: Optional[Callable[[str], None]] = None,
) -> dict[str, str]:
    key, url, mdl = resolve_credentials(api_key, base_url, model)
    repo = Repo.open(name)
    try:
        mods = [m for m in repo.modules() if m["name"] != "(root)"][:max_modules]
        summaries: dict[str, str] = {}
        for m in mods:
            ctx, stats = repo.map(level=2, path_glob=f"{m['name']}/**", budget_tokens=ctx_budget)
            if stats["files_shown"] == 0:
                continue
            user = f"Module: {m['name']} ({m['files']} files)\n\n{ctx}"
            try:
                summaries[m["name"]] = _chat(url, key, mdl, _SYSTEM, user)
            except EnrichmentError as exc:
                summaries[m["name"]] = f"(enrichment failed: {exc})"
            if progress:
                progress(m["name"])
        repo.db.set_meta("enrichment", summaries)
        repo.db.set_meta("enrichment_model", mdl)
        repo.db.commit()
        return summaries
    finally:
        repo.close()
