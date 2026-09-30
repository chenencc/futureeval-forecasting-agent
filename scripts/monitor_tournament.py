"""Read and snapshot FutureEval questions without forecasting or submitting."""

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


def get_json(url: str, token: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.metaculus.com":
        raise ValueError("Refusing non-Metaculus API URL")
    request = Request(url, headers={
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": "futureeval-question-monitor/0.1",
    })
    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code != 429 or attempt == 1:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = min(max(int(retry_after), 1), 30) if retry_after else 15
            except ValueError:
                delay = 15
            print(f"Metaculus rate-limited request; retrying in {delay}s (attempt 2/2)")
            time.sleep(delay)
    raise AssertionError("Unreachable retry state")


def collect_questions(token: str) -> list[dict]:
    # A single bounded request keeps the scheduled monitor safe under API
    # throttling. The tournament currently has far fewer than 100 posts.
    url = API_ROOT + "?" + urlencode({"tournaments": TOURNAMENT, "limit": 100})
    page = get_json(url, token)
    posts = page.get("results") or []
    if page.get("next"):
        print("WARNING: More than 100 questions available; this snapshot contains only the first page.", flush=True)
    return posts


def snapshot_questions(token: str, root: Path = Path("snapshots/monitor")) -> Path:
    now = datetime.now(timezone.utc)
    output = root / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    try:
        posts = collect_questions(token)
    except Exception as exc:
        (output / "index.json").write_text(json.dumps({
            "tournament": TOURNAMENT,
            "retrieved_at_utc": now.isoformat(),
            "question_count": None,
            "questions": [],
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    index = []
    for post in posts:
        post_id = post.get("id")
        if not isinstance(post_id, int):
            continue
        record = {"post": post, "retrieved_at_utc": now.isoformat()}
        (output / f"{post_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append({
            "post_id": post_id,
            "question_id": (post.get("question") or {}).get("id"),
            "title": post.get("title"),
            "status": post.get("status"),
            "open_time": post.get("open_time"),
            "close_time": post.get("close_time"),
            "url": f"https://www.metaculus.com/questions/{post_id}/",
            "snapshot_saved": True,
        })
    (output / "index.json").write_text(json.dumps({
        "tournament": TOURNAMENT,
        "retrieved_at_utc": now.isoformat(),
        "question_count": len(index),
        "questions": index,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(index)} tournament questions to {output}")
    for question in index:
        print(f"{question['post_id']} [{question['status']}] {question['title']}")
    return output


if __name__ == "__main__":
    snapshot_questions(os.environ["METACULUS_TOKEN"])
