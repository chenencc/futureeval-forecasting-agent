"""Inspect a small resolved-question API sample without publishing API data.

This is an access check, not a backtest. It prints only public post metadata and
whether the authenticated account can see a resolution value.
"""

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main() -> None:
    token = os.environ["METACULUS_TOKEN"]
    params = urlencode(
        {
            "tournaments": "spring-aib-2026",
            "statuses": "resolved",
            "forecast_type": "binary",
            "limit": 5,
        }
    )
    url = f"https://www.metaculus.com/api/posts/?{params}"
    request = Request(url, headers={"Authorization": f"Token {token}"})
    try:
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
    except HTTPError as error:
        print(f"Metaculus API returned HTTP {error.code}")
        raise SystemExit(1) from None

    posts = data.get("results", [])
    print(f"Resolved binary posts returned: {len(posts)}")
    print(f"Another page available: {bool(data.get('next'))}")
    for post in posts:
        question = post.get("question") or {}
        record = {
            "post_id": post.get("id"),
            "question_id": question.get("id"),
            "title": post.get("title"),
            "status": post.get("status"),
            "resolved": post.get("resolved"),
            "open_time": post.get("open_time"),
            "resolution_visible": question.get("resolution") is not None,
            "resolution": question.get("resolution"),
            "url": f"https://www.metaculus.com/questions/{post.get('id')}/",
        }
        print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
