from django.urls import path

from . import views

urlpatterns = [
    path("foods/search", views.FoodSearchView.as_view(), name="food-search"),
    path("foods/<uuid:id>", views.FoodDetailView.as_view(), name="food-detail"),
]
