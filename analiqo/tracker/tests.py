from django.test import TestCase
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
from django.urls import reverse
import datetime
from .models import AlertSetting, ProductSKU, CompetitorPriceLog

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


class SellerAnalyticsTestCase(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='testpassword')
        self.client.login(username='testuser', password='testpassword')
        
        # Create alert setting
        self.alert_setting = AlertSetting.objects.create(
            user=self.user,
            my_seller_name='MYTHANGLORYRetail'
        )
        
        # Create product SKU
        self.sku = ProductSKU.objects.create(
            user=self.user,
            name='Test SKU',
            pid='TESTPID123',
            lid='TESTLID123',
            base_cost=100.00,
            floor_price=120.00
        )
        
        # Create price logs representing a sync
        # Competitor 1: WickiShop at 150
        # Competitor 2 (Us): MYTHANGLORYRetail at 140 (holds Buy Box)
        # Competitor 3: DIGIINDIA1 at 130 (cheaper, but doesn't hold Buy Box)
        CompetitorPriceLog.objects.create(
            product_sku=self.sku,
            seller_name='WickiShop',
            final_price=150.00,
            has_buybox=False
        )
        self.our_log = CompetitorPriceLog.objects.create(
            product_sku=self.sku,
            seller_name='MYTHANGLORYRetail',
            final_price=140.00,
            has_buybox=True
        )
        CompetitorPriceLog.objects.create(
            product_sku=self.sku,
            seller_name='DIGIINDIA1',
            final_price=130.00,
            has_buybox=False
        )

    def test_settings_page_get_post(self):
        # Test GET settings
        url = reverse('settings')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MYTHANGLORYRetail')
        
        # Test POST settings
        response = self.client.post(url, {
            'my_seller_name': 'NewSellerName',
            'email_alerts_enabled': False,
            'dashboard_alerts_enabled': True,
            'alert_on_undercut': True
        })
        self.assertEqual(response.status_code, 302) # Redirect to settings
        self.alert_setting.refresh_from_db()
        self.assertEqual(self.alert_setting.my_seller_name, 'NewSellerName')
        self.assertFalse(self.alert_setting.email_alerts_enabled)

    def test_analytics_seller_perspective(self):
        url = reverse('analytics')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_seller_name'])
        self.assertEqual(response.context['my_seller_name'], 'MYTHANGLORYRetail')
        self.assertEqual(response.context['our_wins_count'], 1)
        self.assertEqual(response.context['our_active_listings'], 1)
        
        # Check pricing positions
        pricing_positions = response.context['pricing_positions']
        self.assertEqual(len(pricing_positions), 1)
        
        pos = pricing_positions[0]
        self.assertEqual(pos['status'], 'Premium Winner') # We hold Buy Box but DIGIINDIA1 (130) is cheaper
        self.assertEqual(pos['our_price'], 140.00)
        self.assertEqual(pos['lowest_price'], 130.00)

    def test_sku_update_rules(self):
        url = reverse('sku_update_rules', args=[self.sku.pk])
        response = self.client.post(url, {
            'base_cost': 110.00,
            'floor_price': 125.00
        })
        self.assertEqual(response.status_code, 302) # Redirect back to detail
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.base_cost, 110.00)
        self.assertEqual(self.sku.floor_price, 125.00)

    def test_buybox_lost_alert_dispatch(self):
        from .tasks import fetch_single_sku
        from .models import NotificationLog
        from unittest.mock import patch

        # Mock FlipkartScraper.fetch_sellers to return competitor holding buybox
        mocked_response = [
            {'seller_name': 'WickiShop', 'seller_id': 'w1', 'seller_rating': 4.1, 'final_price': 130.00, 'mrp': 150.00, 'is_selected': True},
            {'seller_name': 'MYTHANGLORYRetail', 'seller_id': 'm1', 'seller_rating': 4.2, 'final_price': 140.00, 'mrp': 150.00, 'is_selected': False}
        ]
        
        with patch('tracker.services.FlipkartScraper.fetch_sellers', return_value=mocked_response):
            # Initially, the setup has us as the buybox owner in setUp.
            # We call fetch_single_sku, which will scraper/simulate sync where WickiShop wins
            fetch_single_sku(self.sku.id)
            
            # Check if alert got generated
            alerts = NotificationLog.objects.filter(product_sku=self.sku, alert_type=NotificationLog.AlertType.BUYBOX_LOST)
            self.assertEqual(alerts.count(), 1)
            self.assertIn('lost the Buy Box', alerts.first().message)

    def test_add_sku_optional_fields(self):
        from unittest.mock import patch
        url = reverse('add_sku')
        
        # Mock fetch_sellers
        mocked_response = [
            {'seller_name': 'SellerA', 'seller_id': 's1', 'seller_rating': 4.0, 'final_price': 200.00, 'mrp': 250.00, 'is_selected': True}
        ]
        
        # 1. Add SKU with blank optional fields
        with patch('tracker.services.FlipkartScraper.fetch_sellers', return_value=mocked_response):
            response = self.client.post(url, {
                'url': 'https://www.flipkart.com/some-product/p/itm123?pid=TESTPIDXYZ123&lid=LSTTESTLIDXYZ123',
                'base_cost': '',
                'floor_price': ''
            })
            self.assertEqual(response.status_code, 302)
            
            # Check created SKU
            new_sku = ProductSKU.objects.get(pid='TESTPIDXYZ123')
            self.assertIsNone(new_sku.base_cost)
            self.assertIsNone(new_sku.floor_price)
            
        # 2. Add SKU with specified optional fields
        with patch('tracker.services.FlipkartScraper.fetch_sellers', return_value=mocked_response):
            response = self.client.post(url, {
                'url': 'https://www.flipkart.com/some-product/p/itm456?pid=TESTPIDXYZ456&lid=LSTTESTLIDXYZ456',
                'base_cost': '150.00',
                'floor_price': '180.00'
            })
            self.assertEqual(response.status_code, 302)
            
            # Check created SKU
            new_sku_2 = ProductSKU.objects.get(pid='TESTPIDXYZ456')
            self.assertEqual(float(new_sku_2.base_cost), 150.00)
            self.assertEqual(float(new_sku_2.floor_price), 180.00)



