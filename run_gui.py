"""Unified entry point for the TikTok Streak Fluent desktop app & scheduled bot runner.

Usage:
    python run_gui.py                  Launch the PySide6 Fluent desktop UI
    python run_gui.py --smoke-test     Verify GUI startup under headless/offscreen CI
    python run_gui.py --run-bot        Execute streak automation (used by Task Scheduler)
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys


def _bootstrap_sys_path() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
    else:
        root = Path(__file__).resolve().parent
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TikTok Streak Desktop App")
    parser.add_argument(
        "--run-bot",
        action="store_true",
        help="Run streak automation without launching the desktop window.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Instantiate MainWindow(smoke_test=True), verify pages, and exit cleanly.",
    )
    parser.add_argument(
        "--headful",
        "--visible",
        action="store_true",
        dest="headful",
        help="Show the browser window during --run-bot execution.",
    )
    parser.add_argument(
        "--only-account",
        default=None,
        help="Run only the named account during --run-bot.",
    )
    parser.add_argument(
        "--only-friend",
        default=None,
        help="Send only to the specified friend handle during --run-bot.",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Override configuration & secrets directory.",
    )
    return parser.parse_args(argv)


def _is_system_dir(path: Path) -> bool:
    low = os.path.normcase(str(path.resolve()))
    return "\\windows\\system32" in low or "\\windows\\syswow64" in low


def _has_manual_config(directory: Path) -> bool:
    return (directory / ".env").is_file() or (directory / "accounts.json").is_file()


def _run_bot_mode(args: argparse.Namespace, root_dir: Path) -> int:
    # Imported lazily here so --run-bot never imports PySide6 / qfluentwidgets.
    from gui.services.local_runner import LocalRunner
    from gui.services.storage import ConfigStore, SecretStore, redact_secrets

    store = ConfigStore(args.config_dir)
    secrets = SecretStore(store.config_dir)
    store.config_dir.mkdir(parents=True, exist_ok=True)
    state = store.load()

    extra_secrets: list[str] = []
    try:
        extra_secrets = LocalRunner._collect_secrets(state, secrets)
    except Exception:
        extra_secrets = []

    class _RedactingFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return redact_secrets(super().format(record), extra_secrets=extra_secrets)

    redacting_fmt = _RedactingFormatter(
        "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    file_handler = logging.FileHandler(store.config_dir / "scheduled_run.log", encoding="utf-8")
    file_handler.setFormatter(redacting_fmt)
    log_handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(redacting_fmt)
        log_handlers.append(stream_handler)
    logging.basicConfig(
        level=logging.INFO,
        handlers=log_handlers,
        force=True,
    )
    logger = logging.getLogger("tiktok-streak")
    prev_cwd = Path.cwd()

    try:
        has_gui_accounts = bool(state.accounts) and any(
            bool(secrets.get_account_cookie(acc.id).strip()) for acc in state.accounts
        )

        if not has_gui_accounts:
            candidate_dirs = [Path.cwd()]
            if state.workspace_dir:
                candidate_dirs.append(Path(state.workspace_dir))
            if getattr(sys, "frozen", False):
                candidate_dirs.append(Path(sys.executable).resolve().parent)
            else:
                candidate_dirs.append(root_dir)

            manual_dir = next((d for d in candidate_dirs if d.is_dir() and _has_manual_config(d)), None)
            if manual_dir is not None:
                if manual_dir != Path.cwd():
                    os.chdir(manual_dir)
                import main as bot_main

                bot_argv: list[str] = []
                if args.headful:
                    bot_argv.append("--headful")
                if args.only_account:
                    bot_argv.extend(["--only-account", args.only_account])
                if args.only_friend:
                    bot_argv.extend(["--only-friend", args.only_friend])
                try:
                    bot_main.cli_main(bot_argv)
                    return 0
                except SystemExit as exc:
                    return 0 if exc.code in (0, None) else int(exc.code) if isinstance(exc.code, int) else 1

        # Ensure writable working directory for debug screenshots when launched from System32.
        if _is_system_dir(Path.cwd()):
            if state.workspace_dir and Path(state.workspace_dir).is_dir():
                os.chdir(state.workspace_dir)
            else:
                os.chdir(store.config_dir)

        result = LocalRunner().run(
            state,
            secrets,
            only_account=args.only_account,
            only_friend=args.only_friend,
            headless=(False if args.headful else None),
        )
        if not result.get("ok"):
            if result.get("error"):
                logger.error("Bot run failed: %s", result["error"])
            return 1
        return 0
    finally:
        try:
            if Path.cwd() != prev_cwd and prev_cwd.is_dir():
                os.chdir(prev_cwd)
        except OSError:
            pass
        logging.getLogger().removeHandler(file_handler)
        try:
            file_handler.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    root_dir = _bootstrap_sys_path()
    args = _parse_args(argv)

    if args.run_bot:
        return _run_bot_mode(args, root_dir)

    # Import Qt & Fluent UI only when running the desktop GUI or --smoke-test.
    from gui.app import main as gui_main

    return gui_main(smoke_test=args.smoke_test, config_dir=args.config_dir)


if __name__ == "__main__":
    raise SystemExit(main())
