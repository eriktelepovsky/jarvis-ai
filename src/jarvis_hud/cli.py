import argparse

from jarvis_hud import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jarvis", description="Personal AI daily briefing dashboard."
    )
    parser.add_argument("--version", action="version", version=f"Jarvis {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="Run the web dashboard")
    serve.add_argument("--host", help="Interface to bind (default: JARVIS_HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="Port to listen on (default: JARVIS_PORT or 8000)")
    serve.add_argument(
        "--reload", action="store_true", help="Restart on code changes (development)"
    )
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        from jarvis_hud.config import Settings

        settings = Settings()
        uvicorn.run(
            "jarvis_hud.app:create_app",
            factory=True,
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
