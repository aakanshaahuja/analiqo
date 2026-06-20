from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .models import AlertSetting, ProductSKU

class EmailSignupForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'Enter your email',
            'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Create password',
            'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
        })
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password',
            'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
        })
    )

    def clean_email(self):
        email = self.cleaned_data.get('email').lower()
        if User.objects.filter(username=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and confirm_password and password != confirm_password:
            raise ValidationError("Passwords do not match.")
        return cleaned_data

    def save(self):
        email = self.cleaned_data.get('email')
        password = self.cleaned_data.get('password')
        # Store email in both username and email fields
        user = User.objects.create_user(username=email, email=email, password=password)
        return user


class EmailLoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'Enter your email',
            'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter password',
            'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
        })
    )


class SettingsForm(forms.ModelForm):
    class Meta:
        model = AlertSetting
        fields = ['my_seller_name', 'bg_job_frequency', 'email_alerts_enabled', 'dashboard_alerts_enabled', 'alert_on_undercut']
        widgets = {
            'my_seller_name': forms.TextInput(attrs={
                'placeholder': 'Enter your store name exactly (e.g. RetailNet)',
                'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
            }),
            'bg_job_frequency': forms.NumberInput(attrs={
                'placeholder': 'Enter frequency in hours (e.g. 24)',
                'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm',
                'min': 1
            }),
            'email_alerts_enabled': forms.CheckboxInput(attrs={
                'class': 'rounded border-slate-300 text-brand-600 focus:ring-brand-500 h-4 w-4'
            }),
            'dashboard_alerts_enabled': forms.CheckboxInput(attrs={
                'class': 'rounded border-slate-300 text-brand-600 focus:ring-brand-500 h-4 w-4'
            }),
            'alert_on_undercut': forms.CheckboxInput(attrs={
                'class': 'rounded border-slate-300 text-brand-600 focus:ring-brand-500 h-4 w-4'
            }),
        }


class SKURulesForm(forms.ModelForm):
    class Meta:
        model = ProductSKU
        fields = ['base_cost', 'floor_price']
        widgets = {
            'base_cost': forms.NumberInput(attrs={
                'placeholder': 'Enter base cost',
                'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
            }),
            'floor_price': forms.NumberInput(attrs={
                'placeholder': 'Enter minimum selling price',
                'class': 'w-full bg-white border border-slate-300 rounded-xl px-4 py-3 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-all shadow-sm'
            }),
        }
