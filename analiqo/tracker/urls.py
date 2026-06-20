from django.urls import path
from .views import (
    LandingView, LogoutView, DashboardView, AddSKUView, 
    SKUDetailView, SKUSyncView, AnalyticsView, MarkNotificationReadView,
    SettingsView, SKUUpdateRulesView
)

urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('add/', AddSKUView.as_view(), name='add_sku'),
    path('sku/<int:pk>/', SKUDetailView.as_view(), name='sku_detail'),
    path('sku/<int:pk>/sync/', SKUSyncView.as_view(), name='sku_sync'),
    path('sku/<int:pk>/update-rules/', SKUUpdateRulesView.as_view(), name='sku_update_rules'),
    path('analytics/', AnalyticsView.as_view(), name='analytics'),
    path('settings/', SettingsView.as_view(), name='settings'),
    path('notifications/mark-read/<int:pk>/', MarkNotificationReadView.as_view(), name='mark_notification_read'),
]
