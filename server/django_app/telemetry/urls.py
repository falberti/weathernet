from django.urls import path

from .views import IngestView

urlpatterns = [
    path("ingest", IngestView.as_view(), name="ingest"),
]
