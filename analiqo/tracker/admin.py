from django.contrib import admin
from .models import ProductSKU, CompetitorPriceLog, AlertSetting, NotificationLog

@admin.register(ProductSKU)
class ProductSKUAdmin(admin.ModelAdmin):
    list_display = ('name', 'pid', 'user', 'base_cost', 'floor_price', 'is_active', 'last_fetched_at')
    list_filter = ('is_active', 'user')
    search_fields = ('name', 'pid', 'lid')

@admin.register(CompetitorPriceLog)
class CompetitorPriceLogAdmin(admin.ModelAdmin):
    list_display = ('product_sku', 'seller_name', 'final_price', 'has_buybox', 'timestamp')
    list_filter = ('has_buybox', 'timestamp')
    search_fields = ('seller_name', 'product_sku__name', 'product_sku__pid')

@admin.register(AlertSetting)
class AlertSettingAdmin(admin.ModelAdmin):
    list_display = ('user', 'email_alerts_enabled', 'dashboard_alerts_enabled', 'alert_on_undercut')

@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'alert_type', 'product_sku', 'is_read', 'created_at')
    list_filter = ('alert_type', 'is_read', 'created_at')
    search_fields = ('user__username', 'message')
