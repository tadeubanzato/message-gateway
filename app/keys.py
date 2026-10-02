"""Print this install's keys as .env lines, so they can be pinned and survive a rebuild.

    docker compose exec gateway python -m app.keys

Paste the output into the .env next to docker-compose.yml, then run
`docker compose up -d --force-recreate`. Treat the output like a password.
"""

from app import bootstrap


def main() -> None:
    print("# Keep these private. Add them to .env, then: docker compose up -d --force-recreate")
    print(f"SETTINGS_ENCRYPTION_KEY={bootstrap.get('encryption_key')}")
    print(f"TOKEN_HMAC_SECRET={bootstrap.get('token_hmac_secret')}")


if __name__ == "__main__":
    main()
