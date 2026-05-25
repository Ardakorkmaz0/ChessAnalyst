from django import forms

from .models import UserProfile


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['chesscom_username', 'lichess_username', 'timezone_offset']
        labels = {
            'chesscom_username': 'Chess.com username',
            'lichess_username':  'Lichess username',
            'timezone_offset':   'Your timezone (hours from UTC)',
        }
        help_texts = {
            'timezone_offset':
                'Used to display tilt analysis times in your local hours. '
                '0 = UTC, 3 = Turkey, -5 = US Eastern.',
        }
