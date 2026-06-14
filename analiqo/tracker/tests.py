from django.test import TestCase
from django.conf import settings
from django.utils import timezone
import datetime

class TimeZoneTestCase(TestCase):
    def test_timezone_setting(self):
        self.assertEqual(settings.TIME_ZONE, 'Asia/Kolkata')
        self.assertTrue(settings.USE_TZ)

    def test_localtime_conversion(self):
        # Create a datetime object in UTC
        utc_time = datetime.datetime(2026, 6, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # Convert to local timezone
        local_time = timezone.localtime(utc_time)
        # IST is UTC + 5:30, so 12:00 UTC should be 17:30 IST
        self.assertEqual(local_time.hour, 17)
        self.assertEqual(local_time.minute, 30)

