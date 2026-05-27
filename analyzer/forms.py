from django import forms

from .models import UserProfile


class UserProfileForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['timezone_offset'].required = False

    def clean_chesscom_username(self):
        return (self.cleaned_data.get('chesscom_username') or '').strip()

    def clean_lichess_username(self):
        return (self.cleaned_data.get('lichess_username') or '').strip()

    def clean_timezone_offset(self):
        value = self.cleaned_data.get('timezone_offset')
        if value is not None:
            return value
        if self.instance and self.instance.pk:
            return self.instance.timezone_offset
        return UserProfile._meta.get_field('timezone_offset').default

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
