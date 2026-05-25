from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    chesscom_username = models.CharField(max_length=80, blank=True)
    lichess_username = models.CharField(max_length=80, blank=True)
    # Hours offset from UTC. Used to group games by hour-of-day in tilt analysis.
    # 3 = Turkey (UTC+3), 0 = UTC, -5 = US Eastern, etc. Range covers all real zones.
    timezone_offset = models.IntegerField(
        default=3,
        validators=[MinValueValidator(-12), MaxValueValidator(14)],
        help_text="Hours offset from UTC. Used to localize 'hour of day' stats.",
    )

    def __str__(self):
        return f"{self.user.username} profile"
