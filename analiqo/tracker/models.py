from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

class ProductSKU(models.Model):
    """Core product tracking configuration."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tracked_skus')
    name = models.CharField(max_length=255)
    
    # Identifiers
    pid = models.CharField(max_length=100, db_index=True)
    lid = models.CharField(max_length=100)
    target_url = models.URLField(max_length=1024, blank=True)
    
    # Pricing Rules
    base_cost = models.DecimalField(max_digits=10, decimal_places=2, help_text="Seller's actual cost")
    floor_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Minimum allowable selling price")
    
    # State
    is_active = models.BooleanField(default=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'pid')
        verbose_name = "Product SKU"
        verbose_name_plural = "Product SKUs"

    def __str__(self):
        return f"{self.name} ({self.pid})"


class CompetitorPriceLog(models.Model):
    """High-volume historical time-series data for chart rendering."""
    product_sku = models.ForeignKey(ProductSKU, on_delete=models.CASCADE, related_name='price_logs')
    
    seller_name = models.CharField(max_length=255, db_index=True)
    seller_id = models.CharField(max_length=100, blank=True)
    seller_rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    
    final_price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    has_buybox = models.BooleanField(default=False, help_text="Did this seller win the Buy Box at fetch time?")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Crucial index for time-series chart rendering and fast lookups
        indexes = [
            models.Index(fields=['product_sku', '-timestamp']),
        ]
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.product_sku.pid} - {self.seller_name} @ {self.final_price}"


class AlertSetting(models.Model):
    """User preferences for threshold breaches."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='alert_settings')
    email_alerts_enabled = models.BooleanField(default=True)
    dashboard_alerts_enabled = models.BooleanField(default=True)
    # E.g., Alert if competitor price drops below my floor price
    alert_on_undercut = models.BooleanField(default=True) 

    def __str__(self):
        return f"Alert Settings for {self.user.username}"


class NotificationLog(models.Model):
    """Audit trail of triggered alerts."""
    class AlertType(models.TextChoices):
        UNDERCUT = 'UNDERCUT', _('Competitor Undercut')
        BUYBOX_LOST = 'BUYBOX_LOST', _('Buy Box Lost')
        ERROR = 'ERROR', _('Fetch Error')

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    product_sku = models.ForeignKey(ProductSKU, on_delete=models.CASCADE, null=True, blank=True)
    alert_type = models.CharField(max_length=20, choices=AlertType.choices)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
