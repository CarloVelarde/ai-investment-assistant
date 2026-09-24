"""Support running the package with python -m investment_assistant"""

import sys

from investment_assistant.config import get_settings
from investment_assistant.main import main
from investment_assistant.notifications import run_notifications

if len(sys.argv) > 1 and sys.argv[1] == "notifications":
    raise SystemExit(run_notifications(get_settings(), sys.argv[2:]))
main()
