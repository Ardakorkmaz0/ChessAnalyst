from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    chesscom_username = models.CharField(max_length=80, blank=True)
    lichess_username = models.CharField(max_length=80, blank=True)

    def __str__(self):
        return f"{self.user.username} profile"
