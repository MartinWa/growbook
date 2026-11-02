from django.urls import path

from . import views

app_name = "diary"
urlpatterns = [
    path("", views.entry_list, name="list"),
    path("capture/", views.capture, name="capture"),
    path("entries/<int:pk>/", views.entry_detail, name="detail"),
    path("entries/<int:pk>/edit/", views.entry_edit, name="edit"),
    path("entries/<int:pk>/delete/", views.entry_delete, name="delete"),
    path("entries/photos/<int:pk>/edit/", views.photo_edit, name="photo-edit"),
    path("entries/photos/<int:pk>/delete/", views.photo_delete, name="photo-delete"),
]
