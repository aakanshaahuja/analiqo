import json
from django.shortcuts import render, redirect
from django.views.generic import ListView, View
from django.contrib import messages
from django.contrib.auth.models import User
from .models import ProductSKU, CompetitorPriceLog
from .services import FlipkartScraper

class DashboardView(ListView):
    model = ProductSKU
    template_name = 'dashboard/index.html'
    context_object_name = 'skus'

    def get_queryset(self):
        qs = super().get_queryset()
        query = self.request.GET.get('search', '')
        if query:
            qs = qs.filter(name__icontains=query)
        return qs.order_last_fetched() if hasattr(qs, 'order_last_fetched') else qs.order_by('-created_at')

    def get_template_names(self):
        if self.request.headers.get('HX-Request'):
            return ['dashboard/partials/sku_list.html']
        return [self.template_name]

class SKUDetailView(View):
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404
        sku = get_object_or_404(ProductSKU, pk=pk)
        
        # Get chart data for this specific SKU
        logs = CompetitorPriceLog.objects.filter(product_sku=sku, has_buybox=True).order_by('timestamp')[:30]
        labels = [log.timestamp.strftime("%b %d, %H:%M") for log in logs]
        prices = [float(log.final_price) for log in logs]
        
        if not labels:
            labels = ["No Data Yet"]
            prices = [0]
            
        chart_data_json = json.dumps({
            "labels": labels,
            "prices": prices,
            "sku_name": sku.name
        })

        # Get detailed sellers
        latest_log = CompetitorPriceLog.objects.filter(product_sku=sku).order_by('-timestamp').first()
        if latest_log:
            from datetime import timedelta
            # Tighten threshold to 5 seconds to prevent capturing previous manual syncs in the same report
            time_threshold = latest_log.timestamp - timedelta(seconds=5)
            detailed_sellers = CompetitorPriceLog.objects.filter(
                product_sku=sku, 
                timestamp__gte=time_threshold
            ).order_by('final_price')
        else:
            detailed_sellers = []

        context = {
            'sku': sku,
            'chart_data_json': chart_data_json,
            'detailed_sellers': detailed_sellers
        }
        return render(request, 'dashboard/detail.html', context)

class SKUSyncView(View):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        from .tasks import fetch_single_sku
        sku = get_object_or_404(ProductSKU, pk=pk)
        
        try:
            # Synchronously fetch the data
            fetch_single_sku(sku.id)
            messages.success(request, f"Successfully re-synced data for {sku.name}")
        except Exception as e:
            messages.error(request, f"Failed to sync data: {str(e)}")
            
        return redirect('sku_detail', pk=pk)

class AddSKUView(View):
    def post(self, request, *args, **kwargs):
        url = request.POST.get('url', '').strip()
        floor_price = 0  # Automatically defaulted for now
        
        if not url:
            messages.error(request, "URL is required.")
            return redirect('dashboard')
            
        pid, lid = FlipkartScraper.extract_pid_lid(url)
        if not pid or not lid:
            messages.error(request, "Invalid Flipkart URL. Could not extract product ID.")
            return redirect('dashboard')
            
        # We need a user. Since auth isn't fully set up in UI, we'll use the superuser we created
        user = User.objects.first()
        
        # Check if already exists
        if ProductSKU.objects.filter(pid=pid).exists():
            messages.warning(request, f"Product {pid} is already being tracked.")
            return redirect('dashboard')
            
        # Verify product by fetching sellers
        sellers = FlipkartScraper.fetch_sellers(pid, lid)
        if not sellers:
            messages.error(request, "Verification Failed: Could not fetch sellers for this product. Are you sure the URL is correct?")
            return redirect('dashboard')
            
        # Product is valid. Save it.
        # Get product name from URL or fallback
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path.strip('/')
            name = path.split('/')[0].replace('-', ' ').title() if path else f"Product {pid}"
        except:
            name = f"Product {pid}"
            
        sku = ProductSKU.objects.create(
            user=user,
            name=name[:255],
            pid=pid,
            lid=lid,
            target_url=url,
            base_cost=0,
            floor_price=floor_price
        )
        
        # We successfully fetched sellers during verification, let's log them immediately so the chart isn't empty!
        from django.utils import timezone
        for index, seller_data in enumerate(sellers):
            is_buybox = seller_data.get('is_selected', False)
            if index == 0 and not any(s.get('is_selected') for s in sellers):
                is_buybox = True
                
            CompetitorPriceLog.objects.create(
                product_sku=sku,
                seller_name=seller_data['seller_name'],
                seller_id=seller_data['seller_id'],
                seller_rating=seller_data.get('seller_rating'),
                final_price=seller_data['final_price'],
                mrp=seller_data.get('mrp'),
                has_buybox=is_buybox
            )
            
        sku.last_fetched_at = timezone.now()
        sku.save(update_fields=['last_fetched_at'])
        
        messages.success(request, f"Successfully added and verified {name}! Found {len(sellers)} sellers.")
        return redirect('dashboard')
