import logging
from django.utils import timezone
from .models import ProductSKU, CompetitorPriceLog, NotificationLog
from .services import FlipkartScraper

logger = logging.getLogger(__name__)

def trigger_all_sku_fetches():
    """
    Master function: Identifies all active SKUs and dispatches individual fetch tasks.
    In a Celery environment, this runs via Celery Beat, enqueuing fetch_single_sku.delay().
    """
    active_skus = ProductSKU.objects.filter(is_active=True)
    for sku in active_skus:
        fetch_single_sku(sku.id)

def fetch_single_sku(sku_id):
    """
    Isolated worker function for a single SKU. Handles its own exceptions.
    """
    try:
        sku = ProductSKU.objects.get(id=sku_id)
        
        # 1. Acquire Data
        parsed_sellers = FlipkartScraper.fetch_sellers(sku.pid, sku.lid)
        
        if not parsed_sellers:
            logger.warning(f"No sellers found or API failed for {sku.pid}")
            return

        # 2. Process & Store
        for index, seller_data in enumerate(parsed_sellers):
            is_buybox_winner = seller_data.get('is_selected', False)
            if index == 0 and not any(s.get('is_selected') for s in parsed_sellers):
                # Fallback if API doesn't mark selected: assume first is buybox winner
                is_buybox_winner = True
            
            log = CompetitorPriceLog.objects.create(
                product_sku=sku,
                seller_name=seller_data['seller_name'],
                seller_id=seller_data['seller_id'],
                seller_rating=seller_data.get('seller_rating'),
                final_price=seller_data['final_price'],
                mrp=seller_data.get('mrp'),
                has_buybox=is_buybox_winner
            )
            
            # 3. Analyze against rules (Undercutting Alert)
            if is_buybox_winner and log.final_price < sku.floor_price:
                dispatch_undercut_alert(sku, log)

        # Update SKU state
        sku.last_fetched_at = timezone.now()
        sku.save(update_fields=['last_fetched_at'])

    except ProductSKU.DoesNotExist:
        logger.error(f"SKU {sku_id} not found.")
    except Exception as e:
        logger.exception(f"Exception during fetch for SKU {sku_id}: {e}")

def dispatch_undercut_alert(sku, price_log):
    """Creates a notification."""
    msg = f"Alert: {price_log.seller_name} is selling below your floor price at ₹{price_log.final_price}."
    NotificationLog.objects.create(
        user=sku.user,
        product_sku=sku,
        alert_type=NotificationLog.AlertType.UNDERCUT,
        message=msg
    )
