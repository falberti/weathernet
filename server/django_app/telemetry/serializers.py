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


class IngestSerializer(serializers.Serializer):
    probe_id = serializers.UUIDField()
    timestamp = serializers.DateTimeField()
    readings = ReadingSerializer(many=True)
    health = HealthSerializer()
