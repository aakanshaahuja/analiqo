from django.core.management.base import BaseCommand
from tracker.tasks import trigger_all_sku_fetches

class Command(BaseCommand):
    help = 'Trigger synchronous fetch for all active SKUs'

    def handle(self, *args, **options):
        self.stdout.write('Starting manual SKU fetch...')
        try:
            trigger_all_sku_fetches()
            self.stdout.write(self.style.SUCCESS('Successfully fetched all active SKUs'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error fetching SKUs: {e}'))
