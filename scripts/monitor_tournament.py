"""Poll FutureEval without submitting forecasts; save point-in-time snapshots."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

API_ROOT = "https://www.metaculus.com/api/posts/"
TOURNAMENT = "fall-futureeval-2026"
OPEN_PAGE_LIMIT = 30
ARCHIVE_PAGES_PER_RUN = 5
RESEARCH_PER_RUN = 2


def get_json(url: str, token: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.metaculus.com" or not parts.path.startswith("/api/posts/"):
        raise ValueError("Refusing non-Metaculus posts API URL")
    request = Request(url, headers={
        "Authorization": f"Token {token}", "Accept": "application/json",
        "User-Agent": "futureeval-question-monitor/0.2",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                data = json.load(response)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Unexpected Metaculus posts response")
            return data
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = min(max(int(retry_after), 1), 30) if retry_after else 5 * (attempt + 1)
            except ValueError:
                delay = 5 * (attempt + 1)
            print(f"Metaculus HTTP {exc.code}; retrying in {delay}s", flush=True)
            time.sleep(delay)
    raise AssertionError("Unreachable retry state")


def _initial_url(open_only: bool) -> str:
    params = {"tournaments": TOURNAMENT, "limit": 100, "include_description": "true"}
    if open_only:
        params["statuses"] = "open"
    return API_ROOT + "?" + urlencode(params)


def _next_url(value: object) -> str | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("Unexpected pagination URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.netloc != "www.metaculus.com" or parts.path != "/api/posts/":
        raise ValueError("Unexpected pagination destination")
    return value


def collect_pages(token: str, start_url: str, max_pages: int) -> tuple[list[dict], str | None, int]:
    posts: list[dict] = []
    visited: set[str] = set()
    url: str | None = start_url
    pages = 0
    while url and pages < max_pages:
        if url in visited:
            raise ValueError("Metaculus pagination loop")
        visited.add(url)
        page = get_json(url, token)
        posts.extend(row for row in page["results"] if isinstance(row, dict))
        url = _next_url(page.get("next"))
        pages += 1
    return posts, url, pages


def collect_questions(token: str) -> tuple[list[dict], bool]:
    posts, next_url, _ = collect_pages(token, _initial_url(True), OPEN_PAGE_LIMIT)
    return posts, next_url is None


def collect_archive_pages(token: str, start_url: str) -> tuple[list[dict], str | None, int, str | None]:
    posts: list[dict] = []
    url: str | None = start_url
    visited: set[str] = set()
    pages = 0
    while url and pages < ARCHIVE_PAGES_PER_RUN:
        if url in visited:
            return posts, url, pages, "Metaculus pagination loop"
        visited.add(url)
        try:
            page = get_json(url, token)
            next_url = _next_url(page.get("next"))
        except Exception as exc:
            return posts, url, pages, f"{type(exc).__name__}: {exc}"
        posts.extend(row for row in page["results"] if isinstance(row, dict))
        url = next_url
        pages += 1
    return posts, url, pages, None


def _questions(post: dict) -> list[dict]:
    rows = []
    question = post.get("question")
    if isinstance(question, dict) and isinstance(question.get("id"), int):
        rows.append(question)
    group = post.get("group_of_questions")
    if isinstance(group, dict):
        rows.extend(row for row in group.get("questions") or [] if isinstance(row, dict) and isinstance(row.get("id"), int))
    return rows


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "seen_question_ids": [], "researched_question_ids": [], "archive_next_url": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported monitor state schema")
    return state


def snapshot_questions(token: str, root: Path = Path("snapshots/monitor"), *, research: bool = False) -> Path:
    now = datetime.now(timezone.utc)
    output = root / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    state = _load_state(state_path)
    seen = set(state["seen_question_ids"])
    researched = set(state["researched_question_ids"])
    try:
        open_posts, open_complete = collect_questions(token)
    except Exception as exc:
        _write_json(output / "index.json", {"tournament": TOURNAMENT, "retrieved_at_utc": now.isoformat(),
                                            "error": f"Open scan failed: {type(exc).__name__}: {exc}", "questions": []})
        raise

    archive_start = state.get("archive_next_url") or _initial_url(False)
    archive_posts, archive_next, archive_pages, archive_error = collect_archive_pages(token, archive_start)

    posts = {row["id"]: row for row in archive_posts if isinstance(row.get("id"), int)}
    posts.update({row["id"]: row for row in open_posts if isinstance(row.get("id"), int)})
    index: list[dict] = []
    pending_research: list[tuple[dict, dict]] = []
    for post in posts.values():
        questions = _questions(post)
        if not questions:  # Ignore announcements.
            continue
        post_id = post["id"]
        _write_json(output / f"{post_id}.json", {"post": post, "retrieved_at_utc": now.isoformat()})
        for question in questions:
            qid = question["id"]
            is_new = qid not in seen
            is_open = post.get("status") == "open" and question.get("status", "open") == "open"
            index.append({"post_id": post_id, "question_id": qid,
                          "title": question.get("title") or post.get("title"),
                          "type": question.get("type"), "status": question.get("status") or post.get("status"),
                          "url": f"https://www.metaculus.com/questions/{post_id}/",
                          "new": is_new, "open": is_open, "snapshot_saved": True})
            seen.add(qid)
            if is_open and question.get("type") == "binary" and qid not in researched:
                pending_research.append((post, question))

    research_results = []
    if research and open_complete:
        from scripts.retrieval_agent import run_retrieval
        for post, question in pending_research[:RESEARCH_PER_RUN]:
            qid = question["id"]
            try:
                task_directory = root / "retrieval" / str(qid)
                request = {"question": question.get("title") or post.get("title") or "",
                           "resolution_criteria": question.get("resolution_criteria") or post.get("resolution_criteria") or "",
                           "fine_print": question.get("fine_print") or post.get("fine_print") or "", "mode": "live"}
                # Existing task input stays frozen even if the upstream question changes.
                # Updated question snapshots remain available separately for review.
                existing = task_directory / "bundle.json"
                if existing.exists():
                    request = json.loads(existing.read_text(encoding="utf-8"))["request"]
                report = run_retrieval(request, task_directory, os.environ["TAVILY_API_KEY"], os.environ["OPENROUTER_API_KEY"])
                _write_json(output / f"research-{qid}.json", report)
                researched.add(qid)
                research_results.append({"question_id": qid, "status": "saved", "retrieval_status": report["result"]["status"]})
            except Exception as exc:
                research_results.append({"question_id": qid, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})

    _write_json(output / "index.json", {
        "tournament": TOURNAMENT, "retrieved_at_utc": now.isoformat(), "code_commit": os.environ.get("GITHUB_SHA"),
        "question_count": len(index), "new_question_count": sum(row["new"] for row in index),
        "open_scan_complete": open_complete, "open_question_count": sum(row["open"] for row in index),
        "archive_pages_scanned": archive_pages, "archive_cycle_complete": archive_next is None,
        "archive_error": archive_error, "research": research_results, "questions": index,
    })
    state.update({"seen_question_ids": sorted(seen), "researched_question_ids": sorted(researched),
                  "archive_next_url": archive_next, "updated_at_utc": now.isoformat()})
    _write_json(state_path, state)
    print(f"Saved {len(index)} snapshots; {sum(row['new'] for row in index)} new; "
          f"open complete={open_complete}; archive pages={archive_pages}; archive error={archive_error}", flush=True)
    for row in index:
        if row["new"]:
            print(f"NEW {row['question_id']} [{row['status']}] {row['title']}", flush=True)
    if not open_complete:
        raise RuntimeError("Open-question scan exceeded page limit; snapshot is incomplete")
    if archive_error:
        raise RuntimeError(f"Archive scan incomplete: {archive_error}")
    if any(row["status"] == "failed" for row in research_results):
        raise RuntimeError("One or more read-only research snapshots failed; see index.json")
    return output


if __name__ == "__main__":
    snapshot_questions(os.environ["METACULUS_TOKEN"], research=os.environ.get("RUN_READ_ONLY_RESEARCH") == "1")
