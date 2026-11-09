"""Portable Browser storage and upgrades alongside the Rust edition."""
from pathlib import Path


LEGACY_BROWSER_DIRS = ('Refseek-Browser-data', 'SemanticSearch-v2-data')


def shared_data_dir(browser_dir):
    path = Path(browser_dir).expanduser().absolute()
    if path.name.lower() == 'browser' and path.parent.name.lower() == 'data':
        return path.parent
    return path


def env_path(browser_dir):
    return shared_data_dir(browser_dir) / '.env'


def config_file(browser_dir, name):
    path = Path(name)
    if path.is_absolute():
        return path
    shared = shared_data_dir(browser_dir) / path
    # A relative certificate path from an older install can still live
    # inside the relocated Browser directory.
    return shared if shared.exists() else Path(browser_dir) / path


def _regular_directory(path):
    return path.is_dir() and not path.is_symlink() and not path.is_junction()


def _regular_env(path):
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        raise ValueError(f'Refseek expected a regular .env file at {path}.')
    return path.is_file()


def prepare_browser_data(home):
    """Move a portable Browser library without copying or rewriting its index."""
    from .folder_store import library_lock

    home = Path(home).resolve()
    shared = home / 'data'
    browser = shared / 'browser'
    with library_lock(shared, name='.accounts.lock'):
        if browser.exists() or browser.is_symlink() or browser.is_junction():
            if not _regular_directory(browser):
                raise ValueError(f'Refseek expected a regular Browser data directory at {browser}.')
            previous = browser
        else:
            previous = next((home / name for name in LEGACY_BROWSER_DIRS if (home / name).exists()), None)
            if previous is not None and (not _regular_directory(previous) or previous.resolve().parent != home):
                raise ValueError(f'Refseek cannot migrate a linked Browser data folder at {previous}.')
        target_env = shared / '.env'
        has_target_env = _regular_env(target_env)
        if previous is not None:
            old_env = previous / '.env'
            has_old_env = _regular_env(old_env)
            if has_old_env and has_target_env and old_env.read_bytes() != target_env.read_bytes():
                raise ValueError(f'Two different .env files exist at {old_env} and {target_env}. Keep the Browser settings in {target_env} before restarting.')
            if previous != browser:
                destination = browser.resolve()
                if destination.parent != shared.resolve():
                    raise ValueError('The Browser destination is outside the shared data directory.')
                if previous.resolve().anchor.lower() != destination.anchor.lower():
                    raise ValueError(f'The shared data is on another drive. Close Refseek Browser, move {previous} to {destination}, then restart.')
                try:
                    previous.rename(browser)
                except OSError as exc:
                    raise OSError(f'Could not move the Browser library from {previous} to {browser}. Close Refseek Browser before moving the folder.') from exc
            if has_old_env:
                moved_env = browser / '.env'
                if has_target_env:
                    moved_env.unlink()  # The identical file already exists above it.
                else:
                    moved_env.rename(target_env)
        else:
            browser.mkdir(exist_ok=True)
    return browser


def source_data_dir(home):
    shared = Path(home) / 'data'
    browser = shared / 'browser'
    # Preserve explicit source installs that wrote a catalogue directly in data.
    if not browser.exists() and any((shared / name).is_file() for name in ('catalog.json', 'index.json')):
        return shared
    return browser
