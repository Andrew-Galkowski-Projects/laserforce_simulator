"""UX-01 — the auth URLconf, mounted at ``/accounts/`` (ADR-0038).

No ``app_name`` — the project uses flat URL names everywhere. The five names
below are Django's own defaults, so ``LOGIN_URL``,
``PasswordChangeView.success_url`` and ``{% url %}`` all resolve with no extra
configuration.

``LoginRequiredMiddleware`` receives the *resolved* view in ``process_view``,
so an ``include()`` cannot be exempted — each entry is wrapped individually
with ``login_not_required``. ``View.as_view()`` copies ``dispatch.__dict__``
onto the returned callable, which is what makes the wrap work on a CBV.

NO password reset (``password_reset*``): deferred with OAuth, since the deploy
has no mail provider. Recovery is ``manage.py changepassword``.
"""

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import path, reverse_lazy

from . import views
from .forms import EmailAuthenticationForm, StyledPasswordChangeForm

urlpatterns = [
    path(
        "login/",
        login_not_required(
            auth_views.LoginView.as_view(
                template_name="accounts/login.html",
                authentication_form=EmailAuthenticationForm,
                redirect_authenticated_user=True,
            )
        ),
        name="login",
    ),
    # Django 5.x LogoutView is POST-only; the top-nav control is a form.
    path(
        "logout/",
        login_not_required(auth_views.LogoutView.as_view()),
        name="logout",
    ),
    path("register/", views.register, name="register"),
    path(
        "password-change/",
        login_not_required(
            auth_views.PasswordChangeView.as_view(
                template_name="accounts/password_change.html",
                form_class=StyledPasswordChangeForm,
                success_url=reverse_lazy("password_change_done"),
            )
        ),
        name="password_change",
    ),
    path(
        "password-change/done/",
        login_not_required(
            auth_views.PasswordChangeDoneView.as_view(
                template_name="accounts/password_change_done.html"
            )
        ),
        name="password_change_done",
    ),
]
