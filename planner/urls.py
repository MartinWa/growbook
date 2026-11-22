from django.urls import path

from . import views

app_name = "planner"
urlpatterns = [
    path("", views.current_month, name="current"),
    path("<int:year>/<int:month>/", views.month_view, name="month"),
]
