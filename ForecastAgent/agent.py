"""Independent ForecastAgent interface for operators and scheduled jobs."""
import argparse
import json
import os
from pathlib import Path

from ForecastAgent import retrieval_agent
from ForecastAgent.skill_loader import SKILLS
from ForecastAgent.tools.channels import channel_catalog
from ForecastAgent.evidence.intelligence import export_intelligence
from ForecastAgent.evidence.acceptance import collection_acceptance
from ForecastAgent.runtime.task_lock import task_lock

REPO = Path(__file__).resolve().parents[1]


def run_research(request, directory, tavily_key=None, router_key=None):
    """Run or resume the SAME task directory; budgets are owned by retrieval."""
    return retrieval_agent.run_retrieval(request, directory,
                         os.environ.get("TAVILY_API_KEY", "") if tavily_key is None else tavily_key,
                         os.environ.get("OPENROUTER_API_KEY", "") if router_key is None else router_key)


def inspect_task(directory):
    """Read-only: no keys, models, network, mutation or budget changes."""
    bundle = retrieval_agent.run_retrieval({}, directory, "", "", replay=True)
    evidence = bundle.get("evidence", [])
    return {"task_directory": str(Path(directory)), "request": bundle["request"],
            "stage": bundle.get("agent_runtime", {}).get("stage", "legacy"),
            'session_state':bundle.get('session_state'), 'sessions':bundle.get('sessions', []),
            'progress':bundle.get('progress', {}),
            "runtime": bundle.get("agent_runtime"), "loaded_skills": bundle.get("loaded_skills", []),
            "skill_versions": {k: v["sha256"] for k, v in bundle.get("skill_bank", {}).items()},
            "resources": {"tavily_basic_attempts": len(bundle.get("searches", [])),
                          'exa_search_attempts':len(bundle.get('exa_searches', [])),
                          'model_http_attempts':len(bundle.get('model_attempts', [])),
                          "page_fetch_attempts": len(bundle.get("fetch_attempts", [])),
                          "update_http_attempts": len(bundle.get("update_attempts", [])),
                          "basic_extract_batches": len(bundle.get("extract_attempts", []))},
            "result": bundle.get("result"), "evidence": evidence,
            "pipeline": bundle.get("pipeline", "legacy"), "excerpts": bundle.get("excerpts", []),
            "market_snapshot_count": len(bundle.get('market_snapshots', {})), 'acceptance': collection_acceptance(bundle),
            "pages": [{"url": url, "capture_method": page.get("capture_method"),
                       "temporal_status": page.get("temporal_status"),
                       "retrieved_at_utc": page.get("retrieved_at_utc")}
                      for url, page in bundle.get("pages", {}).items()],
            "last_error": bundle.get("last_error_detail")}


class ForecastAgent:
    """Bound workspace interface: callers cannot invent alternate budget roots."""
    def __init__(self, workspace=None):
        self.workspace = Path(workspace or REPO).resolve()

    def task_path(self, task_directory):
        path = (self.workspace / task_directory).resolve()
        if not path.is_relative_to(self.workspace / "snapshots"):
            raise ValueError("Task must stay within this workspace's snapshots directory")
        return path

    def inspect(self, task_directory):
        return inspect_task(self.task_path(task_directory))

    def export(self, task_directory):
        """Export an existing ledger without network calls or budget changes."""
        path = self.task_path(task_directory)
        bundle = retrieval_agent.run_retrieval({}, path, "", "", replay=True)
        return {"path": export_intelligence(bundle, path), "truth_verified": False}

    def acceptance(self, task_directory):
        path = self.task_path(task_directory)
        bundle = retrieval_agent.run_retrieval({}, path, '', '', replay=True)
        return collection_acceptance(bundle)

    def refresh(self, task_directory, urls):
        """Incremental live acquisition under the same exclusive task lock."""
        path = self.task_path(task_directory)
        if not (path / 'bundle.json').exists():
            raise ValueError('Refresh requires an existing ledger')
        with task_lock(path):
            original = json.loads((path / 'bundle.json').read_text(encoding='utf-8'))
            task = retrieval_agent.RetrievalTask(path, original['request'])
            return task.execute('refresh_sources', {'urls': urls}, '')

    def run(self, task_directory, request=None):
        path = self.task_path(task_directory)
        if (path / "bundle.json").exists():
            original = json.loads((path / "bundle.json").read_text(encoding="utf-8"))["request"]
            if request is not None and request != original:
                raise ValueError("Existing input is frozen; refusing changed input or budget reset")
            request = original
        if request is None:
            raise ValueError("A full question object is required for a new task")
        run_research(request, path)
        return self.inspect(task_directory)


def main():
    parser = argparse.ArgumentParser(description="Ultra-led retrieval only")
    parser.add_argument("action", choices=["skills", "channels", "export", "acceptance", "refresh", "inspect", "replay", "run"])
    parser.add_argument("--task-dir")
    parser.add_argument("--input", type=Path)
    parser.add_argument('--urls', nargs='+')
    args = parser.parse_args()
    agent = ForecastAgent()
    if args.action == "skills":
        result = SKILLS
    elif args.action == "channels":
        result = channel_catalog()
    else:
        if not args.task_dir:
            parser.error("--task-dir is required")
        if args.action in {"inspect", "replay"}:
            result = agent.inspect(args.task_dir)
        elif args.action == "export":
            result = agent.export(args.task_dir)
        elif args.action == 'acceptance':
            result = agent.acceptance(args.task_dir)
        elif args.action == 'refresh':
            if not args.urls:
                parser.error('--urls is required for refresh')
            result = agent.refresh(args.task_dir, args.urls)
        else:
            request = json.loads(args.input.read_text(encoding="utf-8")) if args.input else None
            result = agent.run(args.task_dir, request)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
