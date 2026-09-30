"""Basic extraction only; callers own the durable request budget."""
import json
from urllib.request import Request, urlopen


def extract_basic(urls, api_key):
    if not api_key:
        raise ValueError("Tavily key required")
    if not 1 <= len(urls) <= 5:
        raise ValueError("Extract batch must contain 1 to 5 URLs")
    payload = {"urls": urls, "extract_depth": "basic", "format": "markdown",
               "include_usage": True, "timeout": 10}
    request = Request("https://api.tavily.com/extract", data=json.dumps(payload).encode(),
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    # No network retry: even unknown outcomes consume the caller's reservation.
    with urlopen(request, timeout=30) as response:
        return json.load(response)
