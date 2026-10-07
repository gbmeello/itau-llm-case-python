"""`python -m purchase_agent` sobe a API (http://127.0.0.1:8080)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run("purchase_agent.api.app:create_app", factory=True, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
