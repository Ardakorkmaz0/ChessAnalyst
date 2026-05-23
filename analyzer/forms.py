from django import forms

from .models import UserProfile


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['chesscom_username', 'lichess_username']
        labels = {
            'chesscom_username': 'Chess.com username',
            'lichess_username': 'Lichess username',
        }
