from django.core.management.base import BaseCommand, CommandError

from core.backups import BackupError, create_backup
from core.media import MediaBusy


class Command(BaseCommand):
    help = "Create a consistent private operational backup (database, originals, configuration)."

    def add_arguments(self, parser):
        parser.add_argument("destination")
        parser.add_argument("--config", action="append", required=True, dest="configuration")

    def handle(self, *args, **options):
        try:
            manifest = create_backup(options["destination"], options["configuration"])
        except (BackupError, MediaBusy) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"Verified backup created: {len(manifest['files'])} files.")
        )
