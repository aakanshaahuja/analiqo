from django.urls import path
from .views import DashboardView, AddSKUView, SKUDetailView, SKUSyncView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('add/', AddSKUView.as_view(), name='add_sku'),
    path('sku/<int:pk>/', SKUDetailView.as_view(), name='sku_detail'),
    path('sku/<int:pk>/sync/', SKUSyncView.as_view(), name='sku_sync'),
]
