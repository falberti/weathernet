from django.urls import path

from .views import EnrollView, PublicSummaryView

urlpatterns = [
    path("enroll", EnrollView.as_view(), name="enroll"),
    path("public/summary", PublicSummaryView.as_view(), name="public-summary"),
]
