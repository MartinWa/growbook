from django.urls import path

from . import views

app_name = "photos"
urlpatterns = [
    path("<int:pk>/", views.photo_detail, name="detail"),
    path("<int:pk>/<str:variant>/", views.photo_file, name="file"),
]
