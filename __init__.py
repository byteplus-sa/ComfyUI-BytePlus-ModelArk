import sys
import traceback
import logging
import subprocess
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from .nodes.constants import MESSAGES

_original_print_exception = traceback.print_exception
_original_format_exception = traceback.format_exception
_original_logging_error = logging.error

def _byteplus_print_exception(*args, **kwargs):
    """
    Print only the message (no traceback) for plugin exceptions marked with
    byteplus_suppress_traceback.
    """
    exc = None
    if len(args) > 0:
        if isinstance(args[0], BaseException):
            exc = args[0]
        elif isinstance(args[0], type) and issubclass(args[0], BaseException) and len(args) > 1:
            exc = args[1]

    if exc and getattr(exc, "byteplus_suppress_traceback", False):
        f = kwargs.get('file')
        if not f:
            f = sys.stderr
        
        print(f"{exc}", file=f)
        return

    return _original_print_exception(*args, **kwargs)

def _byteplus_format_exception(*args, **kwargs):
    """
    Format plugin exceptions marked with byteplus_suppress_traceback as the
    message only.
    """
    exc = None
    if len(args) >= 2:
        exc = args[1]
    
    if exc and getattr(exc, "byteplus_suppress_traceback", False):
        return [f"{exc}\n"]
    
    return _original_format_exception(*args, **kwargs)

def _byteplus_logging_error(msg, *args, **kwargs):
    """
    Suppress ComfyUI's "!!! Exception during processing !!!" log line for
    plugin exceptions, whose message is already shown.
    """
    if isinstance(msg, str) and msg.startswith("!!! Exception during processing !!!"):
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_value and getattr(exc_value, "byteplus_suppress_traceback", False):
            return

    return _original_logging_error(msg, *args, **kwargs)

traceback.print_exception = _byteplus_print_exception
traceback.format_exception = _byteplus_format_exception
logging.error = _byteplus_logging_error

if sys.platform == 'win32':
    try:
        from asyncio import proactor_events
        
        _original_call_connection_lost = proactor_events._ProactorBasePipeTransport._call_connection_lost
        
        def _silenced_call_connection_lost(self, exc):
            """
            Silence asyncio ConnectionResetError / OSError (winerror 10054) on Windows.
            """
            try:
                _original_call_connection_lost(self, exc)
            except ConnectionResetError:
                pass
            except OSError as e:
                if getattr(e, 'winerror', None) == 10054:
                    pass
                else:
                    raise

        proactor_events._ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost
    except ImportError:
        pass

from comfy_api.latest import ComfyExtension

def get_init_text(key, **kwargs):
    """
    Return a startup message, formatted with kwargs.
    """
    msg = MESSAGES.get(key, key)

    try:
        return msg.format(**kwargs)
    except:
        return msg


def check_and_update_dependencies():
    """
    Install requirements.txt when the BytePlus SDK is missing or older than
    the minimum version.
    """
    package_name = "byteplus-python-sdk-v2[ark]"
    distribution_name = "byteplus-python-sdk-v2"
    minimum_version = "3.0.61"
    requirements_file = Path(__file__).with_name("requirements.txt")
    upgrading_loaded_sdk = False

    try:
        import byteplussdkarkruntime
        try:
            current_version = package_version(distribution_name)
        except PackageNotFoundError:
            current_version = "0"
        def _numeric_version(value):
            parts = []
            for item in str(value).split("."):
                digits = "".join(char for char in item if char.isdigit())
                parts.append(int(digits or 0))
            return tuple((parts + [0, 0, 0])[:3])

        if _numeric_version(current_version) >= _numeric_version(minimum_version):
            return True
        print(
            get_init_text(
                "init_sdk_ver_low", current=current_version, min=minimum_version
            )
        )
        upgrading_loaded_sdk = True
    except ModuleNotFoundError:
        print(get_init_text("init_sdk_not_found", pkg=package_name))
    except Exception as e:
        print(get_init_text("init_dep_check_err", e=e))
        return False

    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(requirements_file),
            ]
        )
        if upgrading_loaded_sdk:
            # The old SDK is already imported in this process; the new one is
            # only picked up after a restart, so don't register nodes against it.
            print(get_init_text("init_sdk_update_ok"))
            return False
        import byteplussdkarkruntime
        print(get_init_text("init_sdk_install_ok"))
        return True
    except Exception as e:
        print(get_init_text("init_sdk_install_fail", e=e))
        return False

_dependencies_ready = check_and_update_dependencies()

if _dependencies_ready:
    from .nodes.nodes_shared import BytePlusAPIClient
    from .nodes.nodes_image import BytePlusSeedream4, BytePlusSeedream5, BytePlusSeedreamLayers
    from .nodes.nodes_video import BytePlusSeedance1, BytePlusSeedance1_5, BytePlusSeedance2, BytePlusVideoQueryTasks, BytePlusProgressTest
    from .nodes.nodes_visual import BytePlusVisualUnderstanding
    from .nodes.quota import BytePlusQuotaSettings

    _registered_nodes = [
        BytePlusAPIClient,
        BytePlusSeedream4,
        BytePlusSeedream5,
        BytePlusSeedreamLayers,
        BytePlusSeedance1,
        BytePlusSeedance1_5,
        BytePlusSeedance2,
        BytePlusVideoQueryTasks,
        BytePlusProgressTest,
        BytePlusVisualUnderstanding,
        BytePlusQuotaSettings,
    ]
else:
    _registered_nodes = []

class BytePlusExtension(ComfyExtension):
    """
    Registers the BytePlus ModelArk nodes.
    """
    async def get_node_list(self) -> list[type]:
        return _registered_nodes

async def comfy_entrypoint() -> ComfyExtension:
    """
    ComfyUI extension entry point.
    """
    return BytePlusExtension()

WEB_DIRECTORY = "./web"
__all__ = ["WEB_DIRECTORY"]
