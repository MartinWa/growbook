from django.urls import path

from . import views

app_name = "economy"
urlpatterns = [
    path("", views.economy_list, name="list"),
    path("expenses/new/", views.expense_form, name="expense-create"),
    path("expenses/<int:pk>/", views.expense_view, name="expense-detail"),
    path("expenses/<int:pk>/edit/", views.expense_form, name="expense-edit"),
    path(
        "expenses/<int:pk>/delete/", views.delete_record, {"kind": "expense"}, name="expense-delete"
    ),
    path("expenses/<int:expense_pk>/allocate/", views.allocation_form, name="allocation-create"),
    path(
        "expenses/<int:expense_pk>/allocations/<int:pk>/edit/",
        views.allocation_form,
        name="allocation-edit",
    ),
    path(
        "allocations/<int:pk>/delete/",
        views.delete_record,
        {"kind": "allocation"},
        name="allocation-delete",
    ),
    path("sales/new/", views.sale_form, name="sale-create"),
    path("sales/<int:pk>/edit/", views.sale_form, name="sale-edit"),
    path("sales/<int:pk>/delete/", views.delete_record, {"kind": "sale"}, name="sale-delete"),
]
