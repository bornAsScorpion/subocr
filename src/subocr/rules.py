"""Track selection: which subtitle streams become which sidecars.

Rules:
- commentary: never processed
- untagged / `und`: skipped, left for manual review
- forced: always processed for wanted languages, unless the forced flag fails
  the cue-density sanity check, in which case the track is treated as full
- SDH/HI: governed by HI_POLICY (default prefer-regular)
- per language and variant, a text track beats a bitmap track, then stream order
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config
from .probe import MediaInfo, Track

FULL, FORCED, HI = "full", "forced", "hi"
COMMENTARY, UNTAGGED, UNSUPPORTED = "commentary", "untagged", "unsupported"

_FORCED_TITLE = re.compile(r"forced", re.IGNORECASE)
_HI_TITLE = re.compile(r"(?i:\bsdh\b|hearing[ -]impaired)|\bHI\b|\bCC\b")


@dataclass(frozen=True)
class Target:
    """One sidecar we intend to produce."""

    language: str  # ISO 639-1
    variant: str  # FULL | FORCED | HI
    track: Track


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    decisions: dict[int, str] = field(default_factory=dict)  # stream index -> reason
    notes: list[str] = field(default_factory=list)


def flagged_forced(track: Track) -> bool:
    return track.forced or bool(_FORCED_TITLE.search(track.title))


def cues_per_minute(track: Track, duration: float | None) -> float | None:
    if track.cues is None or not duration:
        return None
    return track.cues / (duration / 60)


def is_really_forced(track: Track, info: MediaInfo, cfg: Config) -> bool:
    """A forced flag is trusted only if the track is sparse.

    Releases sometimes flag a full dialogue track as forced (seen in the wild: over
    1,100 cues on a PGS track flagged forced). Densities use estimated
    cues, not packets, so PGS (two packets per cue) compares fairly with SRT.
    """
    if not flagged_forced(track):
        return False
    cpm = cues_per_minute(track, info.duration)
    if cpm is not None and cpm > cfg.forced_max_cues_per_min:
        return False
    peers = [
        o.cues
        for o in info.tracks
        if o.index != track.index
        and o.language == track.language
        and o.cues
        and not flagged_forced(o)
    ]
    return not (track.cues and peers and track.cues >= 0.5 * max(peers))


def classify(track: Track, info: MediaInfo, cfg: Config) -> str:
    if track.kind == "other":
        return UNSUPPORTED
    if track.comment or cfg.commentary_pattern.search(track.title):
        return COMMENTARY
    if track.language is None:
        return UNTAGGED
    if is_really_forced(track, info, cfg):
        return FORCED
    if track.hearing_impaired or _HI_TITLE.search(track.title):
        return HI
    return FULL


def _best(tracks: list[Track]) -> Track:
    return min(tracks, key=lambda t: (t.kind != "text", t.ordinal))


def build_plan(info: MediaInfo, cfg: Config) -> Plan:
    plan = Plan()
    cats = {t.index: classify(t, info, cfg) for t in info.tracks}

    for t in info.tracks:
        cat = cats[t.index]
        if cat == UNSUPPORTED:
            plan.decisions[t.index] = f"skip: unsupported codec {t.codec}"
        elif cat == COMMENTARY:
            plan.decisions[t.index] = "skip: commentary"
        elif cat == UNTAGGED:
            plan.decisions[t.index] = "skip: untagged (needs manual review)"
        elif t.language not in cfg.languages:
            plan.decisions[t.index] = f"skip: language {t.language} not wanted"
        if cat == FULL and flagged_forced(t):
            plan.notes.append(
                f"stream {t.index}: forced flag ignored (~{t.cues} cues), treated as full"
            )

    for lang in cfg.languages:
        by_cat = {
            c: [t for t in info.tracks if t.language == lang and cats[t.index] == c]
            for c in (FULL, HI, FORCED)
        }
        chosen: list[tuple[str, Track]] = []
        if by_cat[FULL]:
            chosen.append((FULL, _best(by_cat[FULL])))
        if by_cat[HI] and (
            cfg.hi_policy == "both" or (cfg.hi_policy == "prefer-regular" and not by_cat[FULL])
        ):
            chosen.append((HI, _best(by_cat[HI])))
        if by_cat[FORCED]:
            chosen.append((FORCED, _best(by_cat[FORCED])))

        for variant, t in chosen:
            plan.targets.append(Target(lang, variant, t))
            plan.decisions[t.index] = f"use: {lang} {variant} ({t.kind})"
        for cat, tracks in by_cat.items():
            for t in tracks:
                if t.index not in plan.decisions:
                    reason = (
                        "skip: HI not needed (regular track exists)"
                        if cat == HI
                        else f"skip: another {lang} {cat} track preferred"
                    )
                    plan.decisions[t.index] = reason

    if not plan.targets and cfg.text_fallback == "first-stream":
        for t in info.tracks:
            if t.kind == "text" and cats[t.index] in (FULL, HI) and t.language:
                plan.targets.append(Target(t.language, cats[t.index], t))
                plan.decisions[t.index] = f"use: fallback first text stream ({t.language})"
                break

    return plan
