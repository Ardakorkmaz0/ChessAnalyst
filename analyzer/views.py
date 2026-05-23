from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render


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
