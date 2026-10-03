"""Compatibility entry point for the current isolated practical onboarding check.

No arguments or --help perform no writes. Use --run for the complete browser
exercise; see check_practice_browser.py for isolation and cleanup guarantees.
"""
from check_practice_browser import main

if __name__ == "__main__":
    raise SystemExit(main())
