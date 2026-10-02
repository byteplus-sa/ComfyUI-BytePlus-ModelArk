"""
Default credentials: environment variables and the ``.env`` file in ComfyUI's
user folder, so a workflow needs no API Client node (or any key) in it.

Lookup order for every variable: the process environment first, then the
``.env`` file (like dotenv, a real environment variable wins). The file is
read on every use, so a key saved from the Settings dialog works at once.

This module has no dependency on torch or the node classes, so the routes and
tests can import it on its own.
"""

import os
import tempfile
import threading

from .constants import (
    DEFAULT_REGION,
    MEDIAKIT_API_KEY_ENV,
    REGION_BASE_URLS,
    SPEECH_API_KEY_ENV,
)

ENV_FILE_NAME = ".env"

MODELARK_KEY_ENV = "BYTEPLUS_API_KEY"
REGION_ENV = "BYTEPLUS_REGION"
ACCESS_KEY_ENVS = ("BYTEPLUS_ACCESS_KEY", "BYTEPLUS_ACCESSKEY")
SECRET_KEY_ENVS = ("BYTEPLUS_SECRET_KEY", "BYTEPLUS_SECRETKEY")
SESSION_TOKEN_ENV = "BYTEPLUS_SESSION_TOKEN"

# Credential id (as used by the Settings dialog and the routes) -> variable.
CREDENTIAL_VARS = {
    "modelark": MODELARK_KEY_ENV,
    "speech": SPEECH_API_KEY_ENV,
    "mediakit": MEDIAKIT_API_KEY_ENV,
    "access_key": ACCESS_KEY_ENVS[0],
    "secret_key": SECRET_KEY_ENVS[0],
}
# Other spellings read for a variable. Writing or removing the variable also
# removes them, or a stale alias would keep the old value active.
ALIASES = {ACCESS_KEY_ENVS[0]: ACCESS_KEY_ENVS[1:], SECRET_KEY_ENVS[0]: SECRET_KEY_ENVS[1:]}
# Variables a request may write; everything else in the file is left alone.
WRITABLE_VARS = set(CREDENTIAL_VARS.values()) | {REGION_ENV, SESSION_TOKEN_ENV}

_write_lock = threading.Lock()
_read_cache = {"stamp": None, "values": {}}


def env_file_path():
    """``<ComfyUI user folder>/.env``."""
    try:
        import folder_paths

        user_dir = folder_paths.get_user_directory()
    except Exception:
        user_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "user")
    return os.path.join(user_dir, ENV_FILE_NAME)


def _split_line(line):
    """
    ``(name, rest)`` for a line that sets a variable (optional ``export``
    prefix), else None. Shared by the parser and the writer so both agree on
    which lines set which variable.
    """
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export ") or line.startswith("export\t"):
        line = line[7:].lstrip()
    name, _, rest = line.partition("=")
    name = name.strip()
    return (name, rest.strip()) if name else None


def parse_env_text(text):
    """
    Parse ``NAME=value`` lines: optional ``export``, ``#`` comments, single or
    double quotes (``\\"`` and ``\\\\`` are unescaped inside double quotes).
    A variable set on several lines takes the last value.
    """
    values = {}
    for raw_line in (text or "").splitlines():
        parts = _split_line(raw_line)
        if not parts:
            continue
        name, rest = parts
        if rest[:1] in ('"', "'"):
            quote = rest[0]
            value, index = [], 1
            while index < len(rest):
                char = rest[index]
                if quote == '"' and char == "\\" and index + 1 < len(rest):
                    value.append(rest[index + 1])
                    index += 2
                    continue
                if char == quote:
                    break
                value.append(char)
                index += 1
            values[name] = "".join(value)
        else:
            if " #" in rest:
                rest = rest.split(" #", 1)[0]
            values[name] = rest.strip()
    return values


def read_env_file():
    """The ``.env`` values, re-read only when the file changed."""
    path = env_file_path()
    try:
        stat = os.stat(path)
        stamp = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        _read_cache.update(stamp=None, values={})
        return {}
    if _read_cache["stamp"] == stamp:
        return dict(_read_cache["values"])
    try:
        # utf-8-sig: editors such as Notepad start the file with a BOM.
        with open(path, "r", encoding="utf-8-sig") as handle:
            values = parse_env_text(handle.read())
    except (OSError, UnicodeDecodeError):
        return {}
    _read_cache.update(stamp=stamp, values=values)
    return dict(values)


def get_setting(*names):
    """
    First non-empty value among the variable names: process environment, then
    the .env file. Returns "" when none is set.
    """
    file_values = None
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
        if file_values is None:
            file_values = read_env_file()
        value = (file_values.get(name) or "").strip()
        if value:
            return value
    return ""


def setting_source(*names):
    """Where get_setting finds the value: "environment", "file" or None."""
    file_values = None
    for name in names:
        if (os.environ.get(name) or "").strip():
            return "environment"
        if file_values is None:
            file_values = read_env_file()
        if (file_values.get(name) or "").strip():
            return "file"
    return None


def get_default_region(fallback=DEFAULT_REGION):
    """BYTEPLUS_REGION when it names a known region, else ``fallback``."""
    region = get_setting(REGION_ENV)
    if region in REGION_BASE_URLS:
        return region
    return fallback if fallback in REGION_BASE_URLS else DEFAULT_REGION


def get_asset_credentials():
    """IAM AK/SK (+ session token) from the environment or .env, else None."""
    access_key = get_setting(*ACCESS_KEY_ENVS)
    secret_key = get_setting(*SECRET_KEY_ENVS)
    if access_key and secret_key:
        return {
            "access_key": access_key,
            "secret_key": secret_key,
            "session_token": get_setting(SESSION_TOKEN_ENV),
        }
    return None


def _format_value(value):
    if value and all(c.isalnum() or c in "-_.:/+=@~" for c in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env_file(updates):
    """
    Set (or, for an empty value, remove) variables in the .env file. Other
    lines, comments and variables stay as they are. The write is atomic, and
    the file is private to the user (0600) where the platform supports it.
    Returns the path.
    """
    for name, value in updates.items():
        if name not in WRITABLE_VARS:
            raise ValueError(f"{name} cannot be changed here")
        if value is not None and any(c in value for c in "\r\n\0"):
            raise ValueError(f"{name} must be a single line")

    # A symlinked .env stays a link: the file it points to is replaced.
    path = os.path.realpath(env_file_path())
    drop = {alias for name in updates for alias in ALIASES.get(name, ())}
    with _write_lock:
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            lines = []

        written = set()
        output = []
        for line in lines:
            parts = _split_line(line)
            name = parts[0] if parts else None
            if name in drop:
                continue
            if name in updates:
                # The new value goes where the variable was first set; any later
                # line for it is dropped, since the last one would win on read.
                # An empty value drops every line.
                if name not in written and updates[name]:
                    output.append(f"{name}={_format_value(updates[name])}")
                written.add(name)
                continue
            output.append(line)
        for name, value in updates.items():
            if name not in written and value:
                output.append(f"{name}={_format_value(value)}")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".env.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
                temp.write("\n".join(output) + ("\n" if output else ""))
                # On disk before the swap, so a crash cannot leave an empty file.
                temp.flush()
                os.fsync(temp.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        _read_cache.update(stamp=None, values={})
    return path


def key_hint(value):
    """Short, non-reversible hint for a saved key: its last four characters."""
    value = (value or "").strip()
    if len(value) < 12:
        return "saved"
    return f"ends in {value[-4:]}"


def display_path(path):
    """The path with the home folder shown as ~ (shorter, and no user name on screenshots)."""
    home = os.path.expanduser("~")
    if home and home != "~" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def credential_status():
    """
    What is configured, for the Settings dialog. Never contains a key: only
    whether one is set, where it comes from and its last four characters.
    """
    path = env_file_path()
    status = {"env_file": path, "env_file_display": display_path(path), "credentials": {}}
    for credential, variable in CREDENTIAL_VARS.items():
        names = {"access_key": ACCESS_KEY_ENVS, "secret_key": SECRET_KEY_ENVS}.get(credential, (variable,))
        value = get_setting(*names)
        source = setting_source(*names)
        file_values = read_env_file()
        file_value = next((file_values[name].strip() for name in names if (file_values.get(name) or "").strip()), "")
        status["credentials"][credential] = {
            "configured": bool(value),
            "source": source,
            "hint": key_hint(value) if value else "",
            # The file holds a value (Remove can delete it), even one an environment variable hides.
            "in_file": bool(file_value),
            # An environment variable hides a different value in the file.
            "shadowed": source == "environment" and bool(file_value) and file_value != value,
        }
    access, secret = status["credentials"]["access_key"], status["credentials"]["secret_key"]
    # The asset library credentials work as a pair.
    status["credentials"]["iam"] = {
        "configured": access["configured"] and secret["configured"],
        "source": access["source"] if secret["source"] == access["source"] else (access["source"] or secret["source"]),
        "hint": access["hint"],
        "in_file": access["in_file"] or secret["in_file"],
        "shadowed": access["shadowed"] or secret["shadowed"],
    }
    status["region"] = get_default_region()
    # An environment variable fixes the region: the one picked in Settings would be ignored.
    status["region_source"] = setting_source(REGION_ENV)
    status["regions"] = list(REGION_BASE_URLS)
    return status
