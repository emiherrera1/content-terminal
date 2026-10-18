import os
import sys
import traceback

from .cli import main

try:
    main()
except BaseException as e:
    # On GitHub Actions, surface the failure as an annotation on the run page, so the reason is
    # visible without opening the step log.
    if os.environ.get("GITHUB_ACTIONS") and not (isinstance(e, SystemExit) and e.code in (0, None)):
        msg = str(e.code) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"
        where = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
        loc = f" (at {os.path.basename(where.filename)}:{where.lineno})" if where else ""
        print(f"::error title=terminal failed::{msg[:900]}{loc}".replace("\n", " "), flush=True)
    raise
