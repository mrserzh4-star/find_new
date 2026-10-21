"""Реєстр sink-ів."""

from __future__ import annotations

from .base import Sink
from .files import ConsoleSink, HtmlDigestSink, JsonDumpSink

__all__ = ["Sink", "HtmlDigestSink", "JsonDumpSink", "ConsoleSink", "build"]

_KNOWN = {"html", "json", "console"}


def build(
    run_config: dict,
    sources: list[str] | None = None,
    kinds: dict[str, str] | None = None,
    direction_order: list[str] | None = None,
    health: dict | None = None,
    serve_url: str = "",
    dropped: list | None = None,
    direction_defaults: list | None = None,
    slugs: list | None = None,
    banking: list | None = None,
    aliases: dict | None = None,
) -> list[Sink]:
    enabled = list(run_config.get("sinks", ["html", "json", "console"]))
    unknown = set(enabled) - _KNOWN
    if unknown:
        raise ValueError(
            f"невідомі sinks у конфізі: {', '.join(sorted(unknown))}; "
            f"доступні: {', '.join(sorted(_KNOWN))}"
        )

    sinks: list[Sink] = []
    if "html" in enabled:
        sinks.append(HtmlDigestSink(run_config.get("digest", "digest.html"), sources, kinds,
                                    direction_order, health, serve_url, dropped,
                                    direction_defaults, slugs, banking, aliases))
    if "json" in enabled:
        sinks.append(JsonDumpSink(run_config.get("dump", "out/vacancies.json")))
    if "console" in enabled:
        sinks.append(ConsoleSink())
    return sinks
