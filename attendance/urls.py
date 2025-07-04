from django.urls import path

from .views import home, signup, user_login ,user_logout,register_students,take_attendance,attendance_success,show_attendance

urlpatterns = [
    path('home', home, name='home'),
    path('signup/', signup, name='signup'),
    path('', user_login, name='login'),
    path('logout/', user_logout, name='logout'), 
    path('register/<int:course_id>/', register_students, name='register'),
    path('attendance/<int:course_id>/', take_attendance, name='attendance'),
    path('status/<int:course_id>/', show_attendance, name='show_attendance'),
    path('success/', attendance_success, name='attendance_success'),
    
]


