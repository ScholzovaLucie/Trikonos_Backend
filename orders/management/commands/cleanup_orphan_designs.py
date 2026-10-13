import time

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from orders.models import CustomDesignPlacement

ORPHAN_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DESIGNS_ROOT = 'designs'


class Command(BaseCommand):
    help = (
        'Deletes files under designs/ older than 7 days that no '
        'custom_design_placements.url references. Intended to run daily via cron.'
    )

    def handle(self, *args, **options):
        if not default_storage.exists(DESIGNS_ROOT):
            self.stdout.write('No designs/ directory yet - nothing to clean up.')
            return

        referenced_urls = set(CustomDesignPlacement.objects.values_list('url', flat=True))
        now = time.time()
        deleted = 0

        for year_dir in self._subdirectories(DESIGNS_ROOT):
            year_path = f'{DESIGNS_ROOT}/{year_dir}'
            for month_dir in self._subdirectories(year_path):
                month_path = f'{year_path}/{month_dir}'
                _, filenames = default_storage.listdir(month_path)
                for filename in filenames:
                    file_path = f'{month_path}/{filename}'
                    if default_storage.url(file_path) in referenced_urls:
                        continue
                    modified_at = default_storage.get_modified_time(file_path).timestamp()
                    if now - modified_at < ORPHAN_MAX_AGE_SECONDS:
                        continue
                    default_storage.delete(file_path)
                    deleted += 1

        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} orphaned design file(s).'))

    @staticmethod
    def _subdirectories(path):
        directories, _ = default_storage.listdir(path)
        return directories
