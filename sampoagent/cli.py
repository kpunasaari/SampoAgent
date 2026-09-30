"""Small, platform-neutral command line interface."""

import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import uvicorn

from sampoagent.agents.playwright_adapter import BrowserUnavailable, PlaywrightBrowserAgent
from sampoagent.applications.worker import run_worker_cycle
from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository
from sampoagent.data.lifecycle import (
    MAX_ARCHIVE_BYTES,
    RESTORE_CONFIRMATION,
    LocalRuntimeLock,
    create_backup_archive,
    decrypt_backup_archive,
    database_runtime_lock_path,
    erase_local_data,
    encrypt_backup_archive,
    restore_backup_archive,
    runtime_lock_paths,
)


def initialize(path: str | Path = "sampoagent.db", *, demo: bool = False) -> None:
    repository = Repository(path)
    repository.initialize()
    if demo:
        repository.load_demo()


def _close_browser_safely(browser: PlaywrightBrowserAgent) -> None:
    try:
        browser.close()
    except Exception:
        raise BrowserUnavailable("The isolated employer browser could not be closed safely.") from None


def _run_automation(args: argparse.Namespace) -> None:
    if args.interval_seconds < 5 or args.discovery_interval_minutes < 1:
        raise SystemExit("Worker interval must be at least 5 seconds and discovery interval at least 1 minute.")
    repository = Repository(args.database)
    repository.initialize()
    browser = PlaywrightBrowserAgent(Path(args.storage_dir) / "browser-profile")
    last_discovery = 0.0
    try:
        while True:
            should_discover = not args.no_discover and (
                not args.watch or time.monotonic() - last_discovery >= args.discovery_interval_minutes * 60
            )
            try:
                report = run_worker_cycle(
                    repository,
                    browser,
                    discover=should_discover,
                    storage_dir=Path(args.storage_dir),
                    next_run_seconds=args.interval_seconds if args.watch else None,
                )
            except BrowserUnavailable:
                raise SystemExit("Browser automation is unavailable. Install Playwright and its supported Chromium runtime.") from None
            except Exception:
                raise SystemExit("Automation worker cycle failed. Error details were not written to the console.") from None
            if should_discover:
                last_discovery = time.monotonic()
            print(f"Worker: {report.status}; jobs queued: {report.queued_count}; applications processed: {len(report.results)}; interrupted submissions held: {report.recovered_unknown}; pre-submit preparations recovered: {report.recovered_preparing}.")
            for application_id, outcome in report.results:
                print(f"Application {application_id}: {outcome}")
            if not args.watch:
                break
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("Worker stopped. No in-progress final-submit action will be retried automatically.")
    finally:
        try:
            _close_browser_safely(browser)
        finally:
            repository.connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="sampoagent", description="Local-first job application and career agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init_parser = subcommands.add_parser("init", help="Create the local SQLite database")
    init_parser.add_argument("--database", default="sampoagent.db")
    demo_parser = subcommands.add_parser("demo", help="Load clearly marked synthetic demo data")
    demo_parser.add_argument("--database", default="sampoagent.db")
    run_parser = subcommands.add_parser("run", help="Run on localhost only")
    run_parser.add_argument("--database", default="sampoagent.db")
    run_parser.add_argument("--port", type=int, default=8765)
    run_parser.add_argument("--storage-dir", default="application_data")
    automate_parser = subcommands.add_parser("automate", help="Run one safe application-worker cycle, or keep watching")
    automate_parser.add_argument("--database", default="sampoagent.db")
    automate_parser.add_argument("--storage-dir", default="application_data")
    automate_parser.add_argument("--watch", action="store_true", help="Keep the local worker running until Ctrl+C")
    automate_parser.add_argument("--interval-seconds", type=int, default=30)
    automate_parser.add_argument("--discovery-interval-minutes", type=int, default=60)
    automate_parser.add_argument("--no-discover", action="store_true", help="Process the existing queue without scanning sources")
    login_parser = subcommands.add_parser("browser-login", help="Open the isolated local browser so you can sign in manually")
    login_parser.add_argument("--database", default="sampoagent.db")
    login_parser.add_argument("--storage-dir", default="application_data")
    login_parser.add_argument("--url", default="", help="Optional public HTTPS employer sign-in page")
    backup_parser = subcommands.add_parser("backup", help="Create a verified, encrypted local data backup")
    backup_parser.add_argument("--database", default="sampoagent.db", help="Existing local database; it is not migrated")
    backup_parser.add_argument("--storage-dir", default="application_data")
    backup_parser.add_argument("--output", required=True, help="Private destination .sampobak path")
    restore_parser = subcommands.add_parser("restore", help="Restore an existing local backup while SampoAgent is stopped")
    restore_parser.add_argument("--database", default="sampoagent.db")
    restore_parser.add_argument("--storage-dir", default="application_data")
    restore_parser.add_argument("--archive", required=True)
    restore_parser.add_argument("--confirm", required=True, help=f'Type exactly: {RESTORE_CONFIRMATION}')
    erase_parser = subcommands.add_parser("erase-local-data", help="Permanently delete the selected local database and managed data")
    erase_parser.add_argument("--database", default="sampoagent.db")
    erase_parser.add_argument("--storage-dir", default="application_data")
    erase_parser.add_argument("--confirm", required=True, help="Type exactly: ERASE ALL LOCAL SAMPOAGENT DATA")
    esco_parser = subcommands.add_parser("import-esco", help="Import an official ESCO CSV package into the local career index")
    esco_parser.add_argument("package_dir", help="Directory containing the ESCO CSV files downloaded from the European Commission")
    esco_parser.add_argument("--database", default="sampoagent.db")
    esco_parser.add_argument("--version", required=True, help="Dataset version printed by the ESCO download portal, e.g. 1.2.1")
    esco_parser.add_argument("--languages", nargs="+", default=["fi", "en"], help="Language files to index (default: fi en)")
    learning_parser = subcommands.add_parser("learning-summary", help="Print the opted-in, de-identified local outcome learning digest")
    learning_parser.add_argument("--database", default="sampoagent.db", help="Existing local database; this command never creates or migrates it")
    args = parser.parse_args()
    if args.command == "init":
        with LocalRuntimeLock((database_runtime_lock_path(args.database),)):
            initialize(args.database)
    elif args.command == "demo":
        with LocalRuntimeLock((database_runtime_lock_path(args.database),)):
            initialize(args.database, demo=True)
    elif args.command == "run":
        try:
            with LocalRuntimeLock(runtime_lock_paths(args.database, args.storage_dir)):
                uvicorn.run(
                    create_app(args.database, storage_dir=args.storage_dir, manage_automation_worker=True),
                    host="127.0.0.1",
                    port=args.port,
                    access_log=False,
                )
        except RuntimeError:
            raise SystemExit("SampoAgent is already running or could not acquire its local runtime lock.") from None
    elif args.command == "browser-login":
        try:
            with LocalRuntimeLock(runtime_lock_paths(args.database, args.storage_dir)):
                agent = PlaywrightBrowserAgent(Path(args.storage_dir) / "browser-profile", restrict_cross_origin=False)
                try:
                    agent.start()
                    if args.url:
                        agent.open(args.url)
                    print("Sign in manually in the visible employer browser. SampoAgent does not read or save your password.")
                    input("Press Enter here after sign-in is complete; the session stays in the local browser profile. ")
                finally:
                    _close_browser_safely(agent)
        except Exception:
            raise SystemExit("Could not open the isolated employer browser. Check the public HTTPS URL and local browser setup.") from None
    elif args.command == "backup":
        try:
            requested_output = Path(args.output).expanduser()
            if requested_output.is_symlink():
                raise SystemExit("Backup destination must not be a symbolic link.")
            output = requested_output.resolve(strict=False)
            database = Path(args.database).expanduser().resolve(strict=False)
            storage = Path(args.storage_dir).expanduser().resolve(strict=False)
            if output == database or output.is_relative_to(storage):
                raise SystemExit("Choose a backup destination outside the database and managed application storage.")
            if output.exists():
                raise SystemExit("Backup destination already exists; choose a new path so no file is overwritten.")
            repository = Repository.open_read_only(args.database)
        except FileNotFoundError:
            raise SystemExit("An existing local database is required; none was created.") from None
        except (OSError, sqlite3.Error, ValueError):
            raise SystemExit("The existing local database could not be read safely.") from None
        try:
            plain_archive = create_backup_archive(repository, args.storage_dir)
        finally:
            repository.connection.close()
        passphrase = getpass.getpass("Backup passphrase (at least 12 characters): ")
        if passphrase != getpass.getpass("Confirm backup passphrase: "):
            raise SystemExit("Backup passphrases did not match; no file was written.")
        try:
            archive = encrypt_backup_archive(plain_archive, passphrase)
        except ValueError:
            raise SystemExit("Backup passphrase must contain between 12 and 1024 characters.") from None
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sampoagent-backup-", suffix=".tmp", dir=output.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(archive)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary_name, output)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        print(f"Verified local backup written to {output}")
    elif args.command == "restore":
        archive_path = Path(args.archive).expanduser()
        if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES + 128:
            raise SystemExit("Backup file is missing or exceeds the supported archive size.")
        envelope = archive_path.read_bytes()
        try:
            payload = decrypt_backup_archive(envelope, getpass.getpass("Backup passphrase: "))
        except ValueError:
            raise SystemExit("Backup authentication failed or the archive is invalid.") from None
        try:
            with LocalRuntimeLock(runtime_lock_paths(args.database, args.storage_dir)):
                restore_backup_archive(payload, args.database, args.storage_dir, confirmation=args.confirm)
        except (OSError, ValueError, RuntimeError):
            raise SystemExit("Could not restore the selected backup. Check its contents and ensure SampoAgent is stopped.") from None
        print("Local data restored and verified. Start SampoAgent again; employer sites may require sign-in.")
    elif args.command == "erase-local-data":
        try:
            with LocalRuntimeLock(runtime_lock_paths(args.database, args.storage_dir)):
                erase_local_data(args.database, args.storage_dir, confirmation=args.confirm)
        except RuntimeError:
            raise SystemExit("SampoAgent is already running or could not acquire its local runtime lock.") from None
        except (OSError, ValueError):
            raise SystemExit("Could not erase the selected local data. Check the paths and ensure SampoAgent is stopped.") from None
        print("Selected SampoAgent local database and managed application data were erased.")
    elif args.command == "learning-summary":
        try:
            repository = Repository.open_read_only(args.database)
        except FileNotFoundError:
            raise SystemExit("An existing local database is required; none was created.") from None
        except (OSError, sqlite3.Error, ValueError):
            raise SystemExit("The existing local database could not be read safely.") from None
        try:
            if repository.setting("codex_learning_summary_enabled") != "true":
                raise SystemExit("Codex learning-summary sharing is disabled in Settings.")
            print(json.dumps(repository.learning_digest(), ensure_ascii=False, sort_keys=True))
        except sqlite3.Error:
            raise SystemExit("The existing local database could not be read safely.") from None
        finally:
            repository.connection.close()
    elif args.command == "import-esco":
        from sampoagent.careers.taxonomy_import import import_esco_package

        with LocalRuntimeLock((database_runtime_lock_path(args.database),)):
            repository = Repository(args.database)
            repository.initialize()
            try:
                summary = import_esco_package(
                    repository,
                    args.package_dir,
                    version=args.version,
                    languages=args.languages,
                )
            except ValueError:
                raise SystemExit("ESCO package could not be imported. Check the selected CSV files, version, and languages.") from None
            finally:
                repository.connection.close()
        print(
            f"Imported ESCO {summary.version} ({', '.join(summary.languages)}): "
            f"{summary.occupations} occupations, {summary.skills} skills, "
            f"{summary.relationships} relationships; SHA-256 {summary.source_sha256}."
        )
    elif args.command == "automate":
        try:
            with LocalRuntimeLock(runtime_lock_paths(args.database, args.storage_dir)):
                _run_automation(args)
        except BrowserUnavailable:
            raise SystemExit("Automation browser could not be closed safely.") from None
        except RuntimeError:
            raise SystemExit("SampoAgent is already running or could not acquire its local runtime lock.") from None
