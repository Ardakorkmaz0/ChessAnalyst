"""
Generate a .env file with SECRET_KEY and PostgreSQL credentials.

Run this once after cloning the project:
    python create_env.py

If .env already exists, asks before overwriting.
"""
from getpass import getpass
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def prompt(label, default=None, secret=False):
    suffix = f" [{default}]" if default else ""
    raw = getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ")
    return raw.strip() or (default or "")


def main():
    if ENV_FILE.exists():
        answer = input(".env already exists. Overwrite? (y/N): ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    print("\nPostgreSQL database settings (press Enter for defaults):\n")
    db_name = prompt("Database name", default="chessanalyst")
    db_user = prompt("Database user", default="postgres")
    db_password = prompt("Database password", secret=True)
    db_host = prompt("Database host", default="localhost")
    db_port = prompt("Database port", default="5432")

    secret_key = get_random_secret_key()

    content = (
        f"SECRET_KEY={secret_key}\n"
        "DEBUG=True\n"
        "ALLOWED_HOSTS=127.0.0.1,localhost\n"
        "\n"
        "# PostgreSQL\n"
        f"DB_NAME={db_name}\n"
        f"DB_USER={db_user}\n"
        f"DB_PASSWORD={db_password}\n"
        f"DB_HOST={db_host}\n"
        f"DB_PORT={db_port}\n"
    )

    ENV_FILE.write_text(content, encoding="utf-8")
    print(f"\n.env created at {ENV_FILE}")


if __name__ == "__main__":
    main()
