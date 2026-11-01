from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.backups import BackupError, restore_backup


class Command(BaseCommand):
    help = "Restore a private operational backup into a new data directory; regenerate derivatives."

    def add_arguments(self, parser):
        parser.add_argument("backup")
        parser.add_argument("destination")

    def handle(self, *args, **options):
        try:
            restore_backup(options["backup"], options["destination"])
        except (BackupError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Restore verified. Review recovered-configuration "
                "before starting this clean instance."
            )
        )
