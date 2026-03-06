from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health_check, name="health-check"),
    path("foods/search", views.FoodSearchView.as_view(), name="food-search"),
    path("foods/<uuid:id>", views.FoodDetailView.as_view(), name="food-detail"),
    path("foods/request", views.FoodRequestCreateView.as_view(), name="food-request-create"),
    path("foods/request/<uuid:id>", views.FoodRequestDetailView.as_view(), name="food-request-detail"),
]
