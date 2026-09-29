"""Retrieve local memory context without sending it to a model or a cloud service."""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


def recall(query: str, limit: int = 5, base_url: str = "http://127.0.0.1:8765") -> dict:
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Use a local http://127.0.0.1:PORT or http://localhost:PORT server")
    payload = json.dumps({"query": query, "limit": limit}).encode("utf-8")
    request = Request(base_url.rstrip("/") + "/api/recall", data=payload, method="POST",
                      headers={"Content-Type": "application/json"})
    # Keep local memory off any configured HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Recall local memories as JSON (renews returned memories)")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    try:
        result = recall(args.query, args.limit, args.url)
    except HTTPError as error:
        raise SystemExit(f"Recall failed ({error.code}): {error.read().decode('utf-8', 'replace')}") from error
    except (URLError, ValueError) as error:
        raise SystemExit(f"Recall failed: {error}") from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
