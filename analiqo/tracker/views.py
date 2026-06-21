import json
from django.shortcuts import render, redirect
from django.views.generic import ListView, View
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Avg, Q
from .models import ProductSKU, CompetitorPriceLog, NotificationLog, AlertSetting
from .services import FlipkartScraper
from .forms import EmailLoginForm, EmailSignupForm, SettingsForm, SKURulesForm

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
            if latest_buybox and sku.floor_price is not None and latest_buybox.final_price < sku.floor_price:
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
 
class SyncAllView(LoginRequiredMixin, View):
    def post(self, request):
        from .tasks import fetch_single_sku
        active_skus = ProductSKU.objects.filter(user=request.user, is_active=True)
        
        if not active_skus.exists():
            messages.warning(request, "No active products to sync.")
            return redirect('dashboard')
            
        success_count = 0
        fail_count = 0
        
        for sku in active_skus:
            try:
                fetch_single_sku(sku.id)
                success_count += 1
            except Exception as e:
                fail_count += 1
                
        if success_count > 0:
            if fail_count > 0:
                messages.warning(request, f"Synced {success_count} products successfully. Failed to sync {fail_count} products.")
            else:
                messages.success(request, f"Successfully synced all {success_count} active products!")
        else:
            messages.error(request, f"Failed to sync products. All {fail_count} attempts failed.")
            
        return redirect('dashboard')
 
class SKUDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        sku = get_object_or_404(ProductSKU, pk=pk, user=request.user)
        name = sku.name
        try:
            sku.delete()
            messages.success(request, f"Successfully deleted and stopped tracking {name}")
        except Exception as e:
            messages.error(request, f"Failed to delete product: {str(e)}")
        return redirect('dashboard')

class AddSKUView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        url = request.POST.get('url', '').strip()
        base_cost_raw = request.POST.get('base_cost', '').strip()
        floor_price_raw = request.POST.get('floor_price', '').strip()
        
        base_cost = None
        if base_cost_raw:
            try:
                base_cost = float(base_cost_raw)
            except ValueError:
                messages.error(request, "Invalid base cost value.")
                return redirect('dashboard')
                
        floor_price = None
        if floor_price_raw:
            try:
                floor_price = float(floor_price_raw)
            except ValueError:
                messages.error(request, "Invalid floor price value.")
                return redirect('dashboard')
        
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
            base_cost=base_cost,
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
        alert_setting, _ = AlertSetting.objects.get_or_create(user=user)
        my_seller_name = alert_setting.my_seller_name
        
        # Initialize default values
        our_wins_count = 0
        our_active_listings = 0
        total_skus = 0
        optimizable_total = 0
        pricing_positions = []
        threats_list = []
        has_seller_name = False
        
        # Chart 1: Buy Box Share
        buybox_chart_data = None
        
        # General Market Data
        # 1. Buy Box Win Distribution across all competitors (top 10)
        buybox_wins = CompetitorPriceLog.objects.filter(
            product_sku__user=user,
            has_buybox=True
        ).values('seller_name').annotate(wins=Count('id')).order_by('-wins')[:10]
        
        market_labels = [item['seller_name'] for item in buybox_wins]
        market_wins = [item['wins'] for item in buybox_wins]
        market_buybox_chart_data = json.dumps({
            'labels': market_labels,
            'wins': market_wins
        })
        
        if my_seller_name and my_seller_name.strip():
            has_seller_name = True
            clean_name = my_seller_name.strip().lower()
            
            # Count our Buy Box wins
            our_wins_count = CompetitorPriceLog.objects.filter(
                product_sku__user=user,
                has_buybox=True,
                seller_name__iexact=clean_name
            ).count()
            
            # Count other competitors' wins
            other_wins = CompetitorPriceLog.objects.filter(
                product_sku__user=user,
                has_buybox=True
            ).exclude(seller_name__iexact=clean_name).values('seller_name').annotate(wins=Count('id')).order_by('-wins')[:9]
            
            pie_labels = [f"Our Store ({my_seller_name})"] + [item['seller_name'] for item in other_wins]
            pie_wins = [our_wins_count] + [item['wins'] for item in other_wins]
            buybox_chart_data = json.dumps({
                'labels': pie_labels,
                'wins': pie_wins
            })
            
            # 2. Portfolio Price Position Analysis
            active_skus = ProductSKU.objects.filter(user=user, is_active=True)
            total_skus = active_skus.count()
            
            threats_dict = {}
            from django.utils import timezone
            from datetime import timedelta
            
            for sku in active_skus:
                latest_log = CompetitorPriceLog.objects.filter(product_sku=sku).order_by('-timestamp').first()
                if latest_log:
                    # Capture all logs from the same sync (within 5 seconds)
                    time_threshold = latest_log.timestamp - timedelta(seconds=5)
                    current_sync_logs = list(CompetitorPriceLog.objects.filter(
                        product_sku=sku,
                        timestamp__gte=time_threshold
                    ).order_by('final_price'))
                    
                    if not current_sync_logs:
                        continue
                        
                    # Find our log in the current sync
                    our_log = next((x for x in current_sync_logs if x.seller_name.strip().lower() == clean_name), None)
                    # Competitors in this sync (excluding us)
                    competitors = [x for x in current_sync_logs if x.seller_name.strip().lower() != clean_name]
                    lowest_competitor = competitors[0] if competitors else None
                    buybox_winner = next((x for x in current_sync_logs if x.has_buybox), None)
                    buybox_owner_name = buybox_winner.seller_name if buybox_winner else "Unknown"
                    
                    status = "Not Listed"
                    recommendation = "You are not listed on this product."
                    price_difference = None
                    our_price = None
                    lowest_price = lowest_competitor.final_price if lowest_competitor else None
                    is_our_buybox = False
                    opt_val_for_sku = 0
                    
                    if our_log:
                        our_active_listings += 1
                        our_price = our_log.final_price
                        is_our_buybox = our_log.has_buybox
                        
                        if lowest_competitor:
                            price_difference = our_price - lowest_competitor.final_price
                            
                            if is_our_buybox:
                                if price_difference < 0:
                                    status = "Cheapest & Winning"
                                    # Money left on the table analysis
                                    # Find next cheapest competitor
                                    next_cheapest = competitors[0] if competitors else None
                                    if next_cheapest:
                                        diff = next_cheapest.final_price - our_price
                                        if diff > 1:
                                            opt_val_for_sku = diff - 1
                                            optimizable_total += opt_val_for_sku
                                            recommendation = f"Raise price by ₹{opt_val_for_sku} to ₹{next_cheapest.final_price - 1} to maximize margin while retaining Buy Box."
                                        else:
                                            recommendation = "You hold the Buy Box at a highly optimized price."
                                    else:
                                        recommendation = "You are the only seller. Consider raising price to capture margin."
                                else:
                                    status = "Premium Winner"
                                    recommendation = "Winning the Buy Box despite higher price than competitor."
                            else: # We don't have the Buy Box
                                if price_difference <= 0:
                                    status = "Cheapest but Losing"
                                    recommendation = "Lowest price but losing Buy Box. Improve rating or delivery speed."
                                else:
                                    # We are higher
                                    target_price = lowest_competitor.final_price
                                    if sku.floor_price is None or target_price >= sku.floor_price:
                                        status = "Overpriced"
                                        recommendation = f"Lower price by ₹{price_difference} to ₹{target_price} to compete for the Buy Box."
                                    else:
                                        status = "Floor Limit"
                                        recommendation = f"Lowest price (₹{target_price}) is below your floor price (₹{sku.floor_price}). Pricing rules prevent further matching."
                        else:
                            # No competitors, only us listed
                            status = "Cheapest & Winning"
                            is_our_buybox = True
                            recommendation = "No other active sellers. Optimize price for maximum margin."
                            
                        # Threat/Undercutter Tracking
                        if not is_our_buybox and buybox_winner:
                            comp_name = buybox_winner.seller_name
                            if comp_name not in threats_dict:
                                threats_dict[comp_name] = {
                                    'seller_name': comp_name,
                                    'rating': buybox_winner.seller_rating,
                                    'skus_undercutting': 0,
                                    'total_price_gap': 0
                                }
                            threats_dict[comp_name]['skus_undercutting'] += 1
                            if price_difference:
                                threats_dict[comp_name]['total_price_gap'] += float(price_difference)
                                
                    pricing_positions.append({
                        'sku': sku,
                        'our_price': our_price,
                        'lowest_price': lowest_price,
                        'status': status,
                        'price_gap': price_difference,
                        'buybox_owner': buybox_owner_name,
                        'is_our_buybox': is_our_buybox,
                        'recommendation': recommendation,
                        'opt_val': opt_val_for_sku
                    })
            
            # Format and sort threats list
            threats_list = list(threats_dict.values())
            for threat in threats_list:
                if threat['skus_undercutting'] > 0:
                    threat['avg_price_gap'] = round(threat['total_price_gap'] / threat['skus_undercutting'], 1)
                else:
                    threat['avg_price_gap'] = 0
            threats_list = sorted(threats_list, key=lambda x: x['skus_undercutting'], reverse=True)[:5]
            
        else:
            # Fallback if no seller name is configured
            buybox_chart_data = market_buybox_chart_data
            
        # Volatility Index (Most price updates in last 7 days)
        from django.utils import timezone
        from datetime import timedelta
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        volatile_skus = ProductSKU.objects.filter(user=user, is_active=True).annotate(
            log_count=Count('price_logs', filter=Q(price_logs__timestamp__gte=seven_days_ago))
        ).order_by('-log_count')[:5]
        
        # Competitor Directory (General Market Report)
        competitors_data = CompetitorPriceLog.objects.filter(product_sku__user=user).values('seller_name').annotate(
            avg_rating=Avg('seller_rating'),
            log_count=Count('id'),
            sku_count=Count('product_sku', distinct=True)
        ).order_by('-sku_count', '-log_count')[:20]
        
        # Format ratings
        for comp in competitors_data:
            if comp['avg_rating']:
                comp['avg_rating'] = round(comp['avg_rating'], 1)
                
        # Status counts for positioning metrics
        status_counts = {
            'winning': sum(1 for x in pricing_positions if x['is_our_buybox']),
            'overpriced': sum(1 for x in pricing_positions if x['status'] == 'Overpriced'),
            'floor_limit': sum(1 for x in pricing_positions if x['status'] == 'Floor Limit'),
            'not_listed': sum(1 for x in pricing_positions if x['status'] == 'Not Listed'),
            'losing_lowest': sum(1 for x in pricing_positions if x['status'] == 'Cheapest but Losing'),
        }
        
        # Get query parameters
        search_query = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '').strip()
        
        # Filter pricing positions
        filtered_positions = pricing_positions
        if search_query:
            filtered_positions = [
                pos for pos in filtered_positions
                if search_query.lower() in pos['sku'].name.lower() or search_query.lower() in pos['sku'].pid.lower()
            ]
        if status_filter:
            filtered_positions = [
                pos for pos in filtered_positions
                if pos['status'].lower() == status_filter.lower()
            ]
            
        # Paginate
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        page = request.GET.get('page', 1)
        paginator = Paginator(filtered_positions, 10)  # Show 10 per page
        
        try:
            pricing_positions_page = paginator.page(page)
        except PageNotAnInteger:
            pricing_positions_page = paginator.page(1)
        except EmptyPage:
            pricing_positions_page = paginator.page(paginator.num_pages)
            
        # Build query parameters string to persist filters in pagination links
        params = request.GET.copy()
        if 'page' in params:
            params.pop('page')
        querystring = params.urlencode()

        context = {
            'has_seller_name': has_seller_name,
            'my_seller_name': my_seller_name,
            'buybox_chart_data': buybox_chart_data,
            'market_buybox_chart_data': market_buybox_chart_data,
            'volatile_skus': volatile_skus,
            'competitors': competitors_data,
            # Seller perspective additions
            'our_wins_count': our_wins_count,
            'our_active_listings': our_active_listings,
            'total_skus': total_skus,
            'optimizable_total': round(optimizable_total, 2),
            'pricing_positions': pricing_positions_page,
            'search_query': search_query,
            'status_filter': status_filter,
            'querystring': querystring,
            'threats': threats_list,
            'status_counts': status_counts,
        }
        return render(request, 'dashboard/analytics.html', context)


class SettingsView(LoginRequiredMixin, View):
    def get(self, request):
        alert_setting, _ = AlertSetting.objects.get_or_create(user=request.user)
        form = SettingsForm(instance=alert_setting)
        return render(request, 'dashboard/settings.html', {'form': form, 'settings': alert_setting})

    def post(self, request):
        alert_setting, _ = AlertSetting.objects.get_or_create(user=request.user)
        form = SettingsForm(request.POST, instance=alert_setting)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings updated successfully!")
            return redirect('settings')
        return render(request, 'dashboard/settings.html', {'form': form, 'settings': alert_setting})


class SKUUpdateRulesView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        sku = get_object_or_404(ProductSKU, pk=pk, user=request.user)
        form = SKURulesForm(request.POST, instance=sku)
        if form.is_valid():
            form.save()
            messages.success(request, f"Pricing rules updated for {sku.name}!")
        else:
            messages.error(request, "Failed to update pricing rules. Please check the inputs.")
        return redirect('sku_detail', pk=pk)


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
