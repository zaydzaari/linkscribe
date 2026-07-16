#!/usr/bin/env python3
"""Validate the Codex and Claude Code project skills."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    ROOT / ".agents" / "skills" / "transcribe-media" / "SKILL.md",
    ROOT / ".claude" / "skills" / "transcribe-media" / "SKILL.md",
)


def frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: YAML frontmatter is missing")
    try:
        raw = text.split("---\n", 2)[1]
    except IndexError as exc:
        raise ValueError(f"{path}: YAML frontmatter is not closed") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML frontmatter must be a mapping")
    return data


def validate_skill(path: Path) -> None:
    data = frontmatter(path)
    if set(data) != {"name", "description"}:
        raise ValueError(f"{path}: frontmatter must contain only name and description")
    if data["name"] != path.parent.name:
        raise ValueError(f"{path}: skill name must match its directory")
    description = data["description"]
    if not isinstance(description, str) or len(description.strip()) < 80:
        raise ValueError(f"{path}: description must explain behavior and trigger conditions")


def main() -> None:
    for skill in SKILLS:
        validate_skill(skill)

    metadata_path = SKILLS[0].parent / "agents" / "openai.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    interface = metadata.get("interface", {}) if isinstance(metadata, dict) else {}
    required = {"display_name", "short_description", "default_prompt"}
    if not required.issubset(interface):
        raise ValueError(f"{metadata_path}: incomplete interface metadata")
    if "$transcribe-media" not in interface["default_prompt"]:
        raise ValueError(f"{metadata_path}: default prompt must invoke $transcribe-media")

    print("Codex and Claude Code skills are valid.")


if __name__ == "__main__":
    main()
