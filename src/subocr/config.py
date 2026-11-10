"""Configuration, read from environment variables (*arr-style)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .languages import is_known, normalize

DEFAULT_COMMENTARY_REGEX = r"(?i)comment|director|коммент"
DEFAULT_BLACKLIST = "|\\/`_~"

TEXT_MODES = ("auto", "container", "plugin")
TEXT_FALLBACKS = ("none", "first-stream")
HI_POLICIES = ("prefer-regular", "both", "skip")


class ConfigError(ValueError):
    pass


def _languages(value: str, name: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        iso1 = normalize(raw)
        if iso1 is None or not is_known(iso1):
            raise ConfigError(f"{name}: unknown language {raw!r}")
        if iso1 not in out:
            out.append(iso1)
    if not out:
        raise ConfigError(f"{name}: at least one language is required")
    return tuple(out)


def _bool(value: str, name: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name}: expected true/false, got {value!r}")


def _choice(value: str, name: str, choices: tuple[str, ...]) -> str:
    v = value.strip().lower()
    if v not in choices:
        raise ConfigError(f"{name}: expected one of {', '.join(choices)}, got {value!r}")
    return v


@dataclass(frozen=True)
class Config:
    languages: tuple[str, ...] = ("en",)
    default_languages: tuple[str, ...] = ("en",)
    default_marking: bool = False
    text_mode: str = "auto"
    text_fallback: str = "none"
    hi_policy: str = "prefer-regular"
    commentary_regex: str = DEFAULT_COMMENTARY_REGEX
    forced_max_cues_per_min: float = 2.0
    ocr_enabled: bool = True
    ocr_blacklist: str = DEFAULT_BLACKLIST
    scratch_dir: str | None = None
    # CLI-only: write sidecars here instead of next to the video (testing with read-only mounts)
    output_dir: str | None = None
    commentary_pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "commentary_pattern", re.compile(self.commentary_regex))
        except re.error as exc:
            raise ConfigError(f"COMMENTARY_REGEX: {exc}") from exc

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        kwargs: dict[str, object] = {}
        if v := env.get("LANGUAGES"):
            kwargs["languages"] = _languages(v, "LANGUAGES")
        if v := env.get("DEFAULT_LANGUAGES"):
            kwargs["default_languages"] = _languages(v, "DEFAULT_LANGUAGES")
        if v := env.get("DEFAULT_MARKING"):
            kwargs["default_marking"] = _bool(v, "DEFAULT_MARKING")
        if v := env.get("TEXT_MODE"):
            kwargs["text_mode"] = _choice(v, "TEXT_MODE", TEXT_MODES)
        if v := env.get("TEXT_FALLBACK"):
            kwargs["text_fallback"] = _choice(v, "TEXT_FALLBACK", TEXT_FALLBACKS)
        if v := env.get("HI_POLICY"):
            kwargs["hi_policy"] = _choice(v, "HI_POLICY", HI_POLICIES)
        if v := env.get("COMMENTARY_REGEX"):
            kwargs["commentary_regex"] = v
        if v := env.get("FORCED_MAX_CUES_PER_MIN"):
            try:
                kwargs["forced_max_cues_per_min"] = float(v)
            except ValueError as exc:
                raise ConfigError(f"FORCED_MAX_CUES_PER_MIN: not a number: {v!r}") from exc
        if v := env.get("OCR_ENABLED"):
            kwargs["ocr_enabled"] = _bool(v, "OCR_ENABLED")
        if "OCR_BLACKLIST" in env:
            kwargs["ocr_blacklist"] = env["OCR_BLACKLIST"]
        if v := env.get("SCRATCH_DIR"):
            kwargs["scratch_dir"] = v
        return cls(**kwargs)  # type: ignore[arg-type]

    def with_languages(self, value: str) -> Config:
        """Copy with LANGUAGES replaced (CLI --lang)."""
        from dataclasses import replace

        return replace(self, languages=_languages(value, "--lang"))
