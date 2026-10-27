"""Repository-owned Ultra skills, frozen in each task ledger on first use."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "skills"
SKILLS = {
    "question-analysis": "Identify entities, resolution conditions, designated sources and timing boundaries.",
    "economic-data": "Research prices and economic statistics, units, release dates and vintages.",
    "official-events": "Verify corporate, government, legal and election announcements and event stages.",
    "ai-releases": "Research AI model availability, exact versions and dated benchmark leaderboards.",
    "evidence-review": "Audit entity identity, citation support, temporal eligibility and source independence.",
}


def freeze_skills(bundle):
    if "skill_bank" not in bundle:
        bank = {}
        for name, description in SKILLS.items():
            text = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
            bank[name] = {"name": name, "description": description, "content": text,
                          "sha256": hashlib.sha256(text.encode()).hexdigest()}
        bundle["skill_bank"] = bank
    bundle.setdefault("loaded_skills", [])


def catalog(bundle):
    return [{k: item[k] for k in ("name", "description", "sha256")}
            for item in bundle["skill_bank"].values()]


def load_skill(bundle, name):
    if name not in bundle["skill_bank"]:
        raise ValueError("Unknown skill; select a name from the skill catalog")
    if name not in bundle["loaded_skills"]:
        bundle["loaded_skills"].append(name)
    return bundle["skill_bank"][name]
