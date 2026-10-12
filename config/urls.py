from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path
urlpatterns = [path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("", include("clearance.urls"))]
