"""Read and snapshot FutureEval questions without forecasting or submitting."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
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
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def collect_questions(token: str) -> list[dict]:
    url = API_ROOT + "?" + urlencode({"tournaments": TOURNAMENT, "limit": 100})
    posts: list[dict] = []
    while url:
        page = get_json(url, token)
        posts.extend(page.get("results") or [])
        url = page.get("next")
    return posts


def snapshot_questions(token: str, root: Path = Path("snapshots/monitor")) -> Path:
    now = datetime.now(timezone.utc)
    output = root / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    posts = collect_questions(token)
    index = []
    for post in posts:
        post_id = post.get("id")
        if not isinstance(post_id, int):
            continue
        record = {"post": post, "retrieved_at_utc": now.isoformat()}
        try:
            record["detail"] = get_json(f"{API_ROOT}{post_id}/", token)
        except Exception as exc:
            record["detail_error"] = f"{type(exc).__name__}: {exc}"
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
            "detail_saved": "detail" in record,
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
