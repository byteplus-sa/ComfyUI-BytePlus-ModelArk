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
# Variables a request may write; everything else in the file is left alone.
WRITABLE_VARS = set(CREDENTIAL_VARS.values()) | {REGION_ENV}

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


def parse_env_text(text):
    """
    Parse ``NAME=value`` lines: optional ``export``, ``#`` comments, single or
    double quotes (``\\"`` and ``\\\\`` are unescaped inside double quotes).
    """
    values = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[7:].lstrip()
        name, _, rest = line.partition("=")
        name = name.strip()
        if not name:
            continue
        rest = rest.strip()
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
        with open(path, "r", encoding="utf-8") as handle:
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


def get_default_region():
    """BYTEPLUS_REGION when it names a known region, else the default."""
    region = get_setting(REGION_ENV)
    return region if region in REGION_BASE_URLS else DEFAULT_REGION


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

    path = env_file_path()
    with _write_lock:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            lines = []

        pending = dict(updates)
        output = []
        for line in lines:
            stripped = line.strip()
            candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
            name = candidate.partition("=")[0].strip() if "=" in candidate and not candidate.startswith("#") else None
            if name in pending:
                value = pending.pop(name)
                if value:
                    output.append(f"{name}={_format_value(value)}")
                # an empty value drops the line
                continue
            output.append(line)
        for name, value in pending.items():
            if value:
                output.append(f"{name}={_format_value(value)}")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".env.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp:
                temp.write("\n".join(output) + ("\n" if output else ""))
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


def credential_status():
    """
    What is configured, for the Settings dialog. Never contains a key: only
    whether one is set, where it comes from and its last four characters.
    """
    status = {"env_file": env_file_path(), "credentials": {}}
    for credential, variable in CREDENTIAL_VARS.items():
        names = {"access_key": ACCESS_KEY_ENVS, "secret_key": SECRET_KEY_ENVS}.get(credential, (variable,))
        value = get_setting(*names)
        source = setting_source(*names)
        file_value = (read_env_file().get(variable) or "").strip()
        status["credentials"][credential] = {
            "configured": bool(value),
            "source": source,
            "hint": key_hint(value) if value else "",
            # An environment variable hides what is in the file.
            "shadowed": source == "environment" and bool(file_value) and file_value != value,
        }
    access, secret = status["credentials"]["access_key"], status["credentials"]["secret_key"]
    # The asset library credentials work as a pair.
    status["credentials"]["iam"] = {
        "configured": access["configured"] and secret["configured"],
        "source": access["source"] if secret["source"] == access["source"] else (access["source"] or secret["source"]),
        "hint": access["hint"],
        "shadowed": access["shadowed"] or secret["shadowed"],
    }
    status["region"] = get_default_region()
    status["regions"] = list(REGION_BASE_URLS)
    return status
