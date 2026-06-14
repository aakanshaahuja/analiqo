from .models import NotificationLog

def notifications(request):
    """
    Context processor to inject unread notification logs and their count 
    into all template rendering contexts.
    """
    if request.user and request.user.is_authenticated:
        unread_qs = NotificationLog.objects.filter(user=request.user, is_read=False)
        return {
            'unread_notifications': unread_qs.order_by('-created_at')[:10],
            'unread_notifications_count': unread_qs.count()
        }
    return {
        'unread_notifications': [],
        'unread_notifications_count': 0
    }
