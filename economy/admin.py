from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Allocation, Expense, Sale


@admin.register(Expense)
class ExpenseAdmin(ReadOnlyAdmin):
    list_display = ["description", "purchased_on", "purchase_year", "total_price", "category"]


@admin.register(Allocation)
class AllocationAdmin(ReadOnlyAdmin):
    list_display = ["expense", "growing_year", "amount", "scope", "method"]


@admin.register(Sale)
class SaleAdmin(ReadOnlyAdmin):
    list_display = ["description", "sold_on", "quantity", "total_price"]
