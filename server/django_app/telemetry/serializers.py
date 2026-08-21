from rest_framework import serializers


class ReadingSerializer(serializers.Serializer):
    sensor_type = serializers.CharField(max_length=64)
    value = serializers.FloatField()


class HealthSerializer(serializers.Serializer):
    cpu_temp_c = serializers.FloatField(allow_null=True)
    cpu_percent = serializers.FloatField()
    mem_percent = serializers.FloatField()
    disk_percent = serializers.FloatField()
    uptime_seconds = serializers.IntegerField(min_value=0)
    # Optional (unlike the fields above): added after probes were
    # already reporting in the field. required=False + a None default
    # means an old, not-yet-updated probe's payload (with no knowledge
    # of these keys at all) still validates instead of 400ing --
    # only a coordinated probe+server upgrade actually needs to be in
    # lockstep for the rest of this serializer, not this pair.
    undervoltage_now = serializers.BooleanField(allow_null=True, required=False, default=None)
    undervoltage_occurred = serializers.BooleanField(allow_null=True, required=False, default=None)


class IngestSerializer(serializers.Serializer):
    probe_id = serializers.UUIDField()
    timestamp = serializers.DateTimeField()
    readings = ReadingSerializer(many=True)
    health = HealthSerializer()
