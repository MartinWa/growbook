from django.urls import path

from . import views

app_name = "planner"
urlpatterns = [
    path("", views.current_month, name="current"),
    path("<int:year>/<int:month>/", views.month_view, name="month"),
    path("<int:year>/<int:month>/tasks/new/", views.task_new, name="task-new"),
    path("<int:year>/<int:month>/note/", views.note_save, name="note"),
    path("tasks/<int:pk>/edit/", views.task_edit, name="task-edit"),
    path("tasks/<int:pk>/complete/", views.task_complete, name="task-complete"),
    path("tasks/<int:pk>/reopen/", views.task_reopen, name="task-reopen"),
    path("tasks/<int:pk>/delete/", views.task_delete, name="task-delete"),
]
