from __future__ import annotations

import os
import sys

import httpx


def main() -> None:
    base = os.getenv("DOC_LINKER_SMOKE_BASE", "http://localhost:8090")
    live = httpx.get(f"{base}/health/live", timeout=5)
    ready = httpx.get(f"{base}/health/ready", timeout=10)
    print("live:", live.status_code, live.text)
    print("ready:", ready.status_code, ready.text)
    sys.exit(0 if live.is_success and ready.is_success else 1)


if __name__ == "__main__":
    main()
