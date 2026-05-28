from django.urls import path

from . import views

app_name = 'analyzer'

urlpatterns = [
    path('', views.home_both, name='home'),
    path('chesscom/', views.home_chesscom, name='home_chesscom'),
    path('chesscom/search/', views.chesscom_search, name='chesscom_search'),
    path('lichess/', views.home_lichess, name='home_lichess'),
    path('lichess/search/', views.lichess_search, name='lichess_search'),
    path('compare/', views.compare, name='compare'),
    path('profile/', views.profile, name='profile'),
    path('signup/', views.signup, name='signup'),
    path('tilt/range/', views.tilt_range, name='tilt_range'),
]
