"""Write the OpenAPI 3.1 document for the version 1 read-only API."""

from pathlib import Path

from django.core.management.base import BaseCommand

from api.schema import render_document


class Command(BaseCommand):
    help = "Print the version 1 API document as pretty JSON, or write it to --output."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="Write the document to this file instead of standard output.",
        )

    def handle(self, *args, output=None, **options):
        document = render_document()
        if output:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document, encoding="utf-8")
            self.stdout.write(f"Wrote the API document to {path}.")
            return
        self.stdout.write(document, ending="")
