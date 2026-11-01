"""UX-01 — the auth forms (ADR-0038) the Accounts API binds (ADR-0039)."""

from typing import Any

from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.db import transaction
from django.db.models import F

from .models import Invite, InviteRedemption, User

INVALID_INVITE_MESSAGE = "This invite code is invalid or has already been used."
EXPIRED_INVITE_MESSAGE = "This invite has expired."
WRONG_EMAIL_INVITE_MESSAGE = "This invite is for a different email address."


class RegisterForm(UserCreationForm):
    """UX-01 — invite-only self-registration: email + password + confirm, plus
    a redeemable ``Invite`` code. ``save`` spends one of the invite's uses and
    records the redemption.
    """

    class Meta:
        model = User
        fields = ("email",)

    email = forms.EmailField(label="Email")
    password1 = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput
    )
    invite = forms.CharField(
        label="Invite code",
        error_messages={"required": "An invite code is required to register."},
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
    """UX-01 — `PasswordChangeForm` with the house labels."""

    old_password = forms.CharField(
        label="Current password", strip=False, widget=forms.PasswordInput
    )
    new_password1 = forms.CharField(
        label="New password", strip=False, widget=forms.PasswordInput
    )
    new_password2 = forms.CharField(
        label="Confirm new password", strip=False, widget=forms.PasswordInput
    )
