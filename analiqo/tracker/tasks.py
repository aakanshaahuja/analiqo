import logging
from django.utils import timezone
from celery import shared_task
from datetime import timedelta
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

        # Check if user has configured seller name
        from .models import AlertSetting
        alert_setting, _ = AlertSetting.objects.get_or_create(user=sku.user)
        my_seller_name = alert_setting.my_seller_name
        clean_my_seller_name = my_seller_name.strip().lower() if my_seller_name else None

        # Check if we held the Buy Box previously (before creating new logs)
        we_held_buybox_previously = False
        if clean_my_seller_name:
            previous_buybox = CompetitorPriceLog.objects.filter(product_sku=sku, has_buybox=True).order_by('-timestamp').first()
            if previous_buybox and previous_buybox.seller_name.strip().lower() == clean_my_seller_name:
                we_held_buybox_previously = True

        new_buybox_winner = None
        our_seller_log = None

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

            if is_buybox_winner:
                new_buybox_winner = log

            if clean_my_seller_name and log.seller_name.strip().lower() == clean_my_seller_name:
                our_seller_log = log
            
            # 3. Analyze against rules (Undercutting Alert)
            if is_buybox_winner and sku.floor_price is not None and log.final_price < sku.floor_price:
                dispatch_undercut_alert(sku, log)

        # 4. Check for Buy Box Lost alert
        if clean_my_seller_name and we_held_buybox_previously:
            has_lost_buybox = False
            if our_seller_log and not our_seller_log.has_buybox:
                has_lost_buybox = True
            elif not our_seller_log:
                has_lost_buybox = True

            if has_lost_buybox and new_buybox_winner:
                dispatch_buybox_lost_alert(sku, new_buybox_winner)

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

def dispatch_buybox_lost_alert(sku, new_winner_log):
    """Creates a Buy Box Lost notification."""
    msg = f"Alert: You have lost the Buy Box for {sku.name}. The new winner is {new_winner_log.seller_name} at ₹{new_winner_log.final_price}."
    NotificationLog.objects.create(
        user=sku.user,
        product_sku=sku,
        alert_type=NotificationLog.AlertType.BUYBOX_LOST,
        message=msg
    )


@shared_task
def fetch_single_sku_task(sku_id):
    """
    Celery task wrapper for fetching a single SKU's price details.
    """
    fetch_single_sku(sku_id)


@shared_task
def celery_trigger_all_sku_fetches():
    """
    Celery task that runs periodically (e.g. every hour).
    It checks each active SKU, gets the user's bg_job_frequency,
    and runs the fetch task if the duration since last_fetched_at
    exceeds the set frequency (or if it hasn't been fetched yet).
    """
    active_skus = ProductSKU.objects.filter(is_active=True)
    now = timezone.now()
    
    for sku in active_skus:
        from .models import AlertSetting
        setting, _ = AlertSetting.objects.get_or_create(user=sku.user)
        frequency_hours = setting.bg_job_frequency or 24
        
        should_fetch = False
        if not sku.last_fetched_at:
            should_fetch = True
        else:
            time_since_last_fetch = now - sku.last_fetched_at
            if time_since_last_fetch >= timedelta(hours=frequency_hours):
                should_fetch = True
        
        if should_fetch:
            fetch_single_sku_task.delay(sku.id)
