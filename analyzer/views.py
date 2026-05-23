from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import UserProfileForm
from .models import UserProfile


def home_both(request):
    return render(request, 'analyzer/both.html', {'platform': 'both'})


def home_chesscom(request):
    return render(request, 'analyzer/chesscom.html', {'platform': 'chesscom'})


def home_lichess(request):
    return render(request, 'analyzer/lichess.html', {'platform': 'lichess'})


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
