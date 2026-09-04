"""UX-01 — the three auth forms (ADR-0038).

House style: every widget declares its DOM id explicitly in ``attrs`` so the
hand-written ``<label for=...>`` in the template can hardcode it, and every
input carries ``class="form-control"`` (Bootstrap 5.3 from CDN, no project
stylesheet).
"""

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    """UX-01 — `AuthenticationForm` with the house DOM ids and an email input.

    Django keeps the field NAME ``username`` even when ``USERNAME_FIELD`` is
    ``email``; only the widget and label change here. The POST key is
    ``username`` — tests must post ``{"username": <email>, "password": ...}``.
    """

    username = forms.CharField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "id": "login-email",
                "class": "form-control",
                "autocomplete": "email",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "login-password",
                "class": "form-control",
                "autocomplete": "current-password",
            }
        ),
    )


class RegisterForm(UserCreationForm):
    """UX-01 — open self-registration: email + password + confirm."""

    class Meta:
        model = User
        fields = ("email",)

    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "id": "register-email",
                "class": "form-control",
                "autocomplete": "email",
            }
        ),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "register-password1",
                "class": "form-control",
                "autocomplete": "new-password",
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "register-password2",
                "class": "form-control",
                "autocomplete": "new-password",
            }
        ),
    )


class StyledPasswordChangeForm(PasswordChangeForm):
    """UX-01 — `PasswordChangeForm` with the house DOM ids."""

    old_password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "password-change-old-password",
                "class": "form-control",
                "autocomplete": "current-password",
                "autofocus": True,
            }
        ),
    )
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "password-change-new-password1",
                "class": "form-control",
                "autocomplete": "new-password",
            }
        ),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "id": "password-change-new-password2",
                "class": "form-control",
                "autocomplete": "new-password",
            }
        ),
    )
