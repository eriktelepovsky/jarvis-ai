import argparse
from pathlib import Path

from myorb import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="myorb", description="Personal AI daily briefing dashboard."
    )
    parser.add_argument("--version", action="version", version=f"myOrb {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="Run the web dashboard")
    serve.add_argument("--host", help="Interface to bind (default: MYORB_HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="Port to listen on (default: MYORB_PORT or 8000)")
    serve.add_argument(
        "--reload", action="store_true", help="Restart on code changes (development)"
    )
    passkeys = subcommands.add_parser(
        "remove-passkeys",
        help="Remove every passkey, so the password signs you in again (after losing them)",
    )
    passkeys.add_argument("--yes", action="store_true", help="Do not ask to confirm")
    args = parser.parse_args()

    if args.command == "remove-passkeys":
        import sqlite3

        from myorb.config import Settings

        # Straight in the database: the server looks for passkeys on every sign-in.
        database = Settings().data_dir / "myorb.db"
        if not database.exists():
            parser.exit(1, f"No myOrb database at {database}. Set MYORB_DATA_DIR.\n")
        if not args.yes and input("Remove every passkey? [y/N] ").strip().lower() != "y":
            parser.exit(1, "Nothing removed.\n")
        with sqlite3.connect(database) as connection:
            removed = connection.execute("DELETE FROM passkeys").rowcount
        print(f"Removed {removed} passkey(s). Sign in with the password and add a new one.")
        return

    if args.command == "serve":
        from granian import Granian
        from granian.constants import Interfaces

        from myorb.config import Settings
        from myorb.plugins.install import install, plugins_dir

        settings = Settings()
        if settings.plugins:
            install(settings.plugins, plugins_dir(settings.data_dir))
        Granian(
            "myorb.app:create_server_app",
            factory=True,
            interface=Interfaces.ASGI,
            address=args.host or settings.host,
            port=args.port or settings.port,
            log_access=True,
            reload=args.reload,
            # Only code: the data directory (database, plugins) may be inside the project, and
            # a restart on every database write would cut off work in progress, like the brief.
            reload_paths=[Path(__file__).parent],
        ).serve()


if __name__ == "__main__":
    main()
