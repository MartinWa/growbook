#!/usr/bin/env python
"""Django's command-line entry point; defaults to local development only."""

import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "growbook.settings.development")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
