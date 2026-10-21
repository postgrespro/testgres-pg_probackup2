"""
  Usage:
    [pytest]
    log_cli = true
    log_cli_level = 25

  or, without touching pytest config at all, by pointing the
  ``PG_PROBACKUP_CMD_LOG`` environment variable at a file -- every executed
  command is then appended there as a bare, copy-paste-into-a-shell line.
"""

import functools
import logging
import os

# The level between INFO (20) and WARNING (30)
COMMAND = 25
logging.addLevelName(COMMAND, 'COMMAND')

cmd_logger = logging.getLogger('pg_probackup2.commands')

_FILE_HANDLER_INSTALLED = False
_TESTGRES_PATCHED = False


def _maybe_add_file_handler():
    """Attach a file handler to the command logger if PG_PROBACKUP_CMD_LOG is set."""
    global _FILE_HANDLER_INSTALLED
    if _FILE_HANDLER_INSTALLED:
        return
    _FILE_HANDLER_INSTALLED = True

    path = os.environ.get('PG_PROBACKUP_CMD_LOG')
    if not path:
        return
    handler = logging.FileHandler(path)
    handler.setLevel(COMMAND)
    handler.setFormatter(logging.Formatter('%(message)s'))
    cmd_logger.addHandler(handler)
    if cmd_logger.level == logging.NOTSET or cmd_logger.level > COMMAND:
        cmd_logger.setLevel(COMMAND)


def log_command(cmd):
    """Echo a command that is executed.

    Args:
        cmd: the command, either a list/tuple of arguments or an already
             assembled string.
    """
    _maybe_add_file_handler()

    if isinstance(cmd, (list, tuple)):
        line = ' '.join(map(str, cmd))
    else:
        line = str(cmd)

    print([line])

    # bare line for handlers/files (grep- and copy-paste-friendly)
    cmd_logger.log(COMMAND, '%s', line)


def patch_testgres_command_logging():
    """Echo testgres utility invocations (pg_ctl, initdb, pg_basebackup, ...) too.

    testgres routes every external utility through ``utils.execute_utility2``.
    Several submodules (``node``, ``cache``, ``backup``, ...) grab their own
    reference at import time via ``from .utils import execute_utility2``, so it
    is not enough to replace the function in ``utils``.  We therefore:

      1. replace it in ``testgres.utils`` (so any *later* ``from .utils import``
         picks up the wrapper), and
      2. sweep every already-loaded ``testgres.*`` module and replace its
         ``execute_utility2`` attribute when it still points at the original.

    The wrapper only adds an echo and otherwise delegates unchanged, so it is
    safe across testgres versions; any failure to patch is non-fatal.
    """
    global _TESTGRES_PATCHED
    if _TESTGRES_PATCHED:
        return
    _TESTGRES_PATCHED = True

    try:
        import sys
        import testgres.utils as tg_utils
    except Exception as e:  # testgres missing/broken -- nothing to patch
        logging.getLogger(__name__).debug(
            "Could not patch testgres for command logging: %s", e)
        return

    original = getattr(tg_utils, 'execute_utility2', None)
    if original is None or getattr(original, '_pb2_logging_wrapper', False):
        return

    @functools.wraps(original)
    def wrapper(os_ops, args, *a, **kw):
        try:
            log_command(args)
        except Exception:
            # echoing must never break the actual command execution
            pass
        return original(os_ops, args, *a, **kw)

    wrapper._pb2_logging_wrapper = True

    # 1) the source module (covers future ``from .utils import execute_utility2``)
    tg_utils.execute_utility2 = wrapper

    # 2) every already-imported testgres submodule that copied the reference
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith('testgres'):
            continue
        try:
            if getattr(module, 'execute_utility2', None) is original:
                module.execute_utility2 = wrapper
        except Exception:
            pass
