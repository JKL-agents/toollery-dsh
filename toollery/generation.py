from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Protocol

from .evidence import Evidence
from .storage import canonical
from .processes import child_command

PROMPT_VERSION = "toollery_appendix_a1_v1"
# Verbatim wording from Toollery, Appendix A.1; only PDF line wraps removed.
# {N} is substituted for each request. Transport and evidence normalization
# are our adapter choices, not part of the paper's prompt.
SYSTEM_PROMPT = """Generate realistic user-intent queries for multiple skills or tools.
For each skill or tool, write things real users might ask when they need that exact skill or tool.
Use practical task context, constraints, and natural wording.
Avoid copying skill text verbatim.
Return only one valid JSON object. The keys must be the exact skill names.
Each value must be a JSON array of objects.
Each object must contain query, scenario_type, generation_notes.
Return exactly {N} objects per skill."""


@dataclass(frozen=True)
class GenerationResult:
    entries: list[dict[str, str]]
    usage: dict

    @property
    def proxy_queries(self) -> list[str]:
        return [entry["query"] for entry in self.entries]


class Generator(Protocol):
    @property
    def contract(self) -> dict: ...

    def generate(self, evidence: Evidence, count: int) -> GenerationResult: ...


class ProviderFailure(RuntimeError):
    def __init__(self, code):
        self.safe_code = code
        super().__init__(code)


def validate_queries(value: object, count: int) -> list[str]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"Expected exactly {count} proxy queries")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in value):
        raise ValueError("Each proxy query must be a nonempty string of at most 2000 characters")
    queries = [" ".join(item.split()) for item in value]
    if len({item.casefold() for item in queries}) != count:
        raise ValueError("Duplicate proxy queries")
    return queries


def validate_entries(value: object, count: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"Expected exactly {count} manual entries")
    required = ("query", "scenario_type", "generation_notes")
    if any(not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) for key in required) for entry in value):
        raise ValueError("Manual entries require query, scenario_type and generation_notes strings")
    queries = validate_queries([entry["query"] for entry in value], count)
    return [{**{key: entry[key] for key in required}, "query": query} for entry, query in zip(value, queries)]


class MockGenerator:
    """Deterministic plumbing check, NOT a language-model manual generator."""

    @property
    def contract(self) -> dict:
        return {"provider": "mock", "model": "deterministic_mock_v1", "parameters": {}, "mock": True}

    def generate(self, evidence: Evidence, count: int) -> GenerationResult:
        text = canonical(evidence.fields)[:1000]
        return GenerationResult([
            {"query": f"Mock intent {number + 1}: I need a tool for {text}",
             "scenario_type": "mock", "generation_notes": "Deterministic pipeline fixture; not LLM output."}
            for number in range(count)
        ], {})


class CommandGenerator:
    """A local provider bridge: JSON on stdin, JSON on stdout, never shell=True."""

    def __init__(self, config: dict, *, cwd=None, runner=None):
        command = config.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("command must be a nonempty array of program/argument strings")
        if not isinstance(config.get("model"), str) or not config["model"].strip():
            raise ValueError("A command generator requires an explicit model identifier")
        self.command = command
        self.cwd = cwd
        self.runner = runner or subprocess.run
        self.timeout = float(config.get("timeout_seconds", 120))
        if self.timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._contract = {
            "provider": "command",
            "model": config["model"],
            "parameters": config.get("parameters", {}),
            "bridge_version": config.get("bridge_version", "v1"),
            "command": command,
            "mock": False,
        }

    @property
    def contract(self) -> dict:
        return self._contract

    def generate(self, evidence: Evidence, count: int) -> GenerationResult:
        name = evidence.fields.get("name")
        # Missing names get a request-local alias, never a benchmark tool ID.
        name = name.strip() if isinstance(name, str) and name.strip() else "capability_0"
        request = {
            "protocol_version": 2,
            "system_prompt": SYSTEM_PROMPT.replace("{N}", str(count)),
            "manual_prompt_version": PROMPT_VERSION,
            "proxy_query_count": count,
            "normalized_evidence": evidence.payload,
            "capabilities": [{"name": name, "specification": evidence.fields}],
            "model": self.contract["model"],
            "parameters": self.contract["parameters"],
        }
        result = self.runner(child_command(self.command, self.cwd), cwd=self.cwd,
                                input=canonical(request), capture_output=True, text=True,
                                encoding="utf-8", timeout=self.timeout, check=False)
        if result.returncode:
            # Never reflect arbitrary stderr/stdout. Only allow fixed codes.
            code = "bridge_error"
            try:
                candidate = json.loads(result.stdout)["error"]["code"]
                if isinstance(candidate, str) and (candidate in {"configuration_error", "transport_error", "invalid_response"} or re.fullmatch(r"http_[1-5][0-9]{2}", candidate)):
                    code = candidate
            except (ValueError, KeyError, TypeError):
                pass
            raise ProviderFailure(code)
        response = json.loads(result.stdout)
        if not isinstance(response, dict):
            raise ValueError("Provider bridge must return a JSON object")
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("usage must be a JSON object")
        manuals = response.get("manuals")
        if not isinstance(manuals, dict) or set(manuals) != {name}:
            raise ValueError("Provider must return manuals keyed by the exact capability name")
        return GenerationResult(validate_entries(manuals[name], count), usage)


def make_generator(config: dict, *, cwd=None) -> Generator:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockGenerator()
    if provider == "command":
        return CommandGenerator(config, cwd=cwd)
    raise ValueError(f"Unknown generator provider: {provider}")
