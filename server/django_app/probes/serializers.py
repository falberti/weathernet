from rest_framework import serializers

from .models import Probe


class EnrollRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    csr_pem = serializers.CharField()
    wireguard_public_key = serializers.CharField(max_length=44)
    # Purely a sanity check against what the operator declared when
    # creating the token -- mismatches are logged, never rejected (see
    # views.EnrollView).
    detected_hardware_type = serializers.ChoiceField(
        choices=Probe.HardwareType.choices, required=False, allow_null=True, allow_blank=True
    )
