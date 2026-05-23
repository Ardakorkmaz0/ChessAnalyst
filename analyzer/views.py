from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render

from . import chesscom_api, lichess_api
from .forms import UserProfileForm
from .models import UserProfile


def home_both(request):
    return render(request, 'analyzer/both.html', {'platform': 'both'})


def home_chesscom(request):
    """
    Chess.com stats page.

    States the template handles:
      - user not authenticated      → ask to sign in
      - no chess.com username saved → ask to set it on /profile
      - api lookup failed/404       → error card
      - success                     → render real data
    """
    context = {'platform': 'chesscom'}

    if not request.user.is_authenticated:
        context['state'] = 'guest'
        return render(request, 'analyzer/chesscom.html', context)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    chesscom_username = (profile.chesscom_username or '').strip()

    if not chesscom_username:
        context['state'] = 'no_username'
        return render(request, 'analyzer/chesscom.html', context)

    data = chesscom_api.get_player_data(chesscom_username)
    if data is None:
        context['state'] = 'not_found'
        context['attempted_username'] = chesscom_username
        return render(request, 'analyzer/chesscom.html', context)

    context['state'] = 'ok'
    context['player'] = data
    return render(request, 'analyzer/chesscom.html', context)


def home_lichess(request):
    """Lichess stats page — mirrors home_chesscom's state machine."""
    context = {'platform': 'lichess'}

    if not request.user.is_authenticated:
        context['state'] = 'guest'
        return render(request, 'analyzer/lichess.html', context)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    lichess_username = (profile.lichess_username or '').strip()

    if not lichess_username:
        context['state'] = 'no_username'
        return render(request, 'analyzer/lichess.html', context)

    data = lichess_api.get_player_data(lichess_username)
    if data is None:
        context['state'] = 'not_found'
        context['attempted_username'] = lichess_username
        return render(request, 'analyzer/lichess.html', context)

    context['state'] = 'ok'
    context['player'] = data
    return render(request, 'analyzer/lichess.html', context)


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('analyzer:home')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    saved = False

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=user_profile)
        if form.is_valid():
            form.save()
            saved = True
    else:
        form = UserProfileForm(instance=user_profile)

    return render(request, 'analyzer/profile.html', {'form': form, 'saved': saved})
