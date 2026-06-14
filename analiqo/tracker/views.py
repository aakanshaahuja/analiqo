import json
from django.shortcuts import render, redirect
from django.views.generic import ListView, View
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Avg, Q
from .models import ProductSKU, CompetitorPriceLog, NotificationLog
from .services import FlipkartScraper
from .forms import EmailLoginForm, EmailSignupForm

class DashboardView(LoginRequiredMixin, ListView):
    model = ProductSKU
    template_name = 'dashboard/index.html'
    context_object_name = 'skus'

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset().filter(user=user)
        query = self.request.GET.get('search', '')
        if query:
            qs = qs.filter(name__icontains=query)
        return qs.order_last_fetched() if hasattr(qs, 'order_last_fetched') else qs.order_by('-created_at')

    def get_template_names(self):
        if self.request.headers.get('HX-Request'):
            return ['dashboard/partials/sku_list.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        active_skus = ProductSKU.objects.filter(user=user, is_active=True)
        context['total_products'] = active_skus.count()
        
        breaches = 0
        for sku in active_skus:
            latest_buybox = CompetitorPriceLog.objects.filter(product_sku=sku, has_buybox=True).order_by('-timestamp').first()
            if latest_buybox and latest_buybox.final_price < sku.floor_price:
                breaches += 1
                
        # Count unique competitors
        unique_competitors = CompetitorPriceLog.objects.filter(product_sku__user=user).values_list('seller_name', flat=True).distinct().count()
        
        # Average competitor rating
        avg_rating = CompetitorPriceLog.objects.filter(product_sku__user=user, seller_rating__isnull=False).aggregate(Avg('seller_rating'))['seller_rating__avg']
        
        context['active_breaches'] = breaches
        context['unique_competitors'] = unique_competitors
        context['avg_competitor_rating'] = round(avg_rating, 1) if avg_rating else "N/A"
        return context

class SKUDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        from django.shortcuts import get_object_or_404
        sku = get_object_or_404(ProductSKU, pk=pk, user=request.user)
        
        # Get chart data for this specific SKU
        from django.utils import timezone
        logs = CompetitorPriceLog.objects.filter(product_sku=sku, has_buybox=True).order_by('timestamp')[:30]
        labels = [timezone.localtime(log.timestamp).strftime("%b %d, %H:%M") for log in logs]
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

class SKUSyncView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        from .tasks import fetch_single_sku
        sku = get_object_or_404(ProductSKU, pk=pk, user=request.user)
        
        try:
            # Synchronously fetch the data
            fetch_single_sku(sku.id)
            messages.success(request, f"Successfully re-synced data for {sku.name}")
        except Exception as e:
            messages.error(request, f"Failed to sync data: {str(e)}")
            
        return redirect('sku_detail', pk=pk)

class AddSKUView(LoginRequiredMixin, View):
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
            
        user = request.user
        
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


class AnalyticsView(LoginRequiredMixin, View):
    def get(self, request):
        user = request.user
        
        # 1. Buy Box Win Distribution across all competitors (top 10)
        buybox_wins = CompetitorPriceLog.objects.filter(
            product_sku__user=user,
            has_buybox=True
        ).values('seller_name').annotate(wins=Count('id')).order_by('-wins')[:10]
        
        labels = [item['seller_name'] for item in buybox_wins]
        wins = [item['wins'] for item in buybox_wins]
        buybox_chart_data = json.dumps({
            'labels': labels,
            'wins': wins
        })
        
        # 2. Price Volatility Index (Most price updates in last 7 days)
        from django.utils import timezone
        from datetime import timedelta
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        volatile_skus = ProductSKU.objects.filter(user=user, is_active=True).annotate(
            log_count=Count('price_logs', filter=Q(price_logs__timestamp__gte=seven_days_ago))
        ).order_by('-log_count')[:5]
        
        # 3. Competitor Directory
        competitors_data = CompetitorPriceLog.objects.filter(product_sku__user=user).values('seller_name').annotate(
            avg_rating=Avg('seller_rating'),
            log_count=Count('id'),
            sku_count=Count('product_sku', distinct=True)
        ).order_by('-sku_count', '-log_count')[:20]
        
        # Format ratings
        for comp in competitors_data:
            if comp['avg_rating']:
                comp['avg_rating'] = round(comp['avg_rating'], 1)
                
        context = {
            'buybox_chart_data': buybox_chart_data,
            'volatile_skus': volatile_skus,
            'competitors': competitors_data
        }
        return render(request, 'dashboard/analytics.html', context)


class MarkNotificationReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.http import HttpResponse
        from django.shortcuts import get_object_or_404
        user = request.user
        notification = get_object_or_404(NotificationLog, pk=pk, user=user)
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return HttpResponse(status=204)


class LandingView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        context = {
            'login_form': EmailLoginForm(),
            'signup_form': EmailSignupForm(),
            'active_tab': 'login'
        }
        return render(request, 'landing.html', context)

    def post(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
            
        action = request.POST.get('action')
        login_form = EmailLoginForm()
        signup_form = EmailSignupForm()
        active_tab = 'login'

        if action == 'login':
            login_form = EmailLoginForm(request.POST)
            if login_form.is_valid():
                email = login_form.cleaned_data['email']
                password = login_form.cleaned_data['password']
                user = authenticate(request, username=email, password=password)
                if user is not None:
                    login(request, user)
                    messages.success(request, f"Welcome back!")
                    return redirect('dashboard')
                else:
                    messages.error(request, "Invalid email or password.")
            else:
                messages.error(request, "Please correct the errors in the login form.")
                
        elif action == 'signup':
            active_tab = 'signup'
            signup_form = EmailSignupForm(request.POST)
            if signup_form.is_valid():
                user = signup_form.save()
                login(request, user)
                messages.success(request, "Account created successfully! Welcome to Analiqo.")
                return redirect('dashboard')
            else:
                for field, errors in signup_form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field.title()}: {error}")

        context = {
            'login_form': login_form,
            'signup_form': signup_form,
            'active_tab': active_tab
        }
        return render(request, 'landing.html', context)


class LogoutView(View):
    def get(self, request):
        logout(request)
        messages.success(request, "Logged out successfully.")
        return redirect('landing')

    def post(self, request):
        logout(request)
        messages.success(request, "Logged out successfully.")
        return redirect('landing')
