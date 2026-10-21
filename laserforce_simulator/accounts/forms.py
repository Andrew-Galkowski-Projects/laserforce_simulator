"""UX-01 — the three auth forms (ADR-0038).

House style: every widget declares its DOM id explicitly in ``attrs`` so the
hand-written ``<label for=...>`` in the template can hardcode it, and every
input carries ``class="form-control"`` (Bootstrap 5.3 from CDN, no project
stylesheet).
"""

from typing import Any

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)
from django.db import transaction
from django.db.models import F

from .models import Invite, InviteRedemption, User

INVALID_INVITE_MESSAGE = "This invite code is invalid or has already been used."
EXPIRED_INVITE_MESSAGE = "This invite has expired."
WRONG_EMAIL_INVITE_MESSAGE = "This invite is for a different email address."


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
    """UX-01 — invite-only self-registration: email + password + confirm, plus
    a redeemable ``Invite`` code. ``save`` spends one of the invite's uses and
    records the redemption.
    """

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
    invite = forms.CharField(
        label="Invite code",
        error_messages={"required": "An invite code is required to register."},
        widget=forms.TextInput(
            attrs={
                "id": "register-invite",
                "class": "form-control",
                "autocomplete": "off",
            }
        ),
    )

    def clean_invite(self) -> Invite:
        code = self.cleaned_data["invite"].strip()
        invite = Invite.objects.filter(code=code).first()
        if invite is None or not invite.uses_left:
            raise forms.ValidationError(INVALID_INVITE_MESSAGE, code="invalid")
        if invite.is_expired:
            raise forms.ValidationError(EXPIRED_INVITE_MESSAGE, code="expired")
        return invite

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        invite = cleaned.get("invite")
        email = cleaned.get("email")
        if invite and email and invite.email and invite.email.lower() != email.lower():
            self.add_error(
                "invite",
                forms.ValidationError(WRONG_EMAIL_INVITE_MESSAGE, code="wrong_email"),
            )
        return cleaned

    @transaction.atomic
    def save(self, commit: bool = True) -> User:
        user = super().save(commit=commit)
        if commit:
            invite = self.cleaned_data["invite"]
            # Conditional increment: racing redemptions of the last use cannot
            # both win; the loser's Account is rolled back with this atomic.
            claimed = Invite.objects.filter(
                pk=invite.pk, use_count__lt=F("max_uses")
            ).update(use_count=F("use_count") + 1)
            if not claimed:
                raise forms.ValidationError(INVALID_INVITE_MESSAGE, code="invalid")
            InviteRedemption.objects.create(invite=invite, user=user)
        return user


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
