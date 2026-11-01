from django.urls import path

from . import views

app_name = "diary"
urlpatterns = [
    path("", views.entry_list, name="list"),
    path("capture/", views.capture, name="capture"),
    path("entries/<int:pk>/", views.entry_detail, name="detail"),
]
