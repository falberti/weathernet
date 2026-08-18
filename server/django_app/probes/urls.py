from django.urls import path

from .views import EnrollView, PublicHistoryView, PublicSummaryView

urlpatterns = [
    path("enroll", EnrollView.as_view(), name="enroll"),
    path("public/summary", PublicSummaryView.as_view(), name="public-summary"),
    path("public/history", PublicHistoryView.as_view(), name="public-history"),
]
