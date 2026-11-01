from django.core.management.base import BaseCommand, CommandError

from core.cleanup import cleanup_orphans
from core.media import MediaBusy


class Command(BaseCommand):
    help = "List stale unreferenced media; delete only with --apply. Never touches backup storage."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--minimum-age", type=int, default=86400, help="Minimum age in seconds."
        )
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args, **options):
        try:
            candidates = cleanup_orphans(
                apply=options["apply"], minimum_age=options["minimum_age"], limit=options["limit"]
            )
        except (MediaBusy, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        for relative in candidates:
            self.stdout.write(relative)
        verb = "Removed" if options["apply"] else "Would remove"
        self.stdout.write(f"{verb} {len(candidates)} unreferenced bundles/staging directories.")
