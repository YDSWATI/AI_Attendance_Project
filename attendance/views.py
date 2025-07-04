from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from .models import Signup, Course, Student, Attendance
from django.utils import timezone
import re
from word2number import w2n

def signup(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        password = request.POST.get('password')
        dept = request.POST.get('dept')

        if all([name, email, password, dept]):  # Check all fields are filled
            hashed_password = make_password(password)
            Signup.objects.create(name=name, email=email, password=hashed_password, dept=dept)
            return redirect('login')  # Redirect to login after signup

    return render(request, 'signup.html')

def user_login(request):
    if request.method == 'POST':
        email = request.POST['email']
        password = request.POST['password']
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            return redirect('home')  # Redirect to home page after login
        else:
            messages.error(request, "Invalid email or password.")
    
    return render(request, 'login.html')

@login_required
def home(request):
    if request.method == "POST":
        course_name = request.POST.get('course_name')
        if course_name:
            user_instance = Signup.objects.filter(email=request.user.email).first()
            Course.objects.create(name=course_name, user=user_instance)
            return redirect('home')

    user_instance = Signup.objects.filter(email=request.user.email).first()
    courses = Course.objects.filter(user=user_instance)
    return render(request, 'home.html', {'user': user_instance, 'courses': courses})

def user_logout(request):
    logout(request)
    return redirect('login')

@login_required
def register_students(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    students = Student.objects.filter(course=course)

    if request.method == 'POST':
        name = request.POST.get('name')
        roll_number = request.POST.get('roll_number')

        if name and roll_number:
            try:
                # Extract the last two characters from roll_number and convert to an ID
                student_id = int(roll_number[-2:])  # Ensure roll_number has enough characters
                Student.objects.create(
                    course=course,
                    name=name,
                    roll_number=roll_number,
                    student_id=student_id
                )
                return redirect('register', course_id=course.id)  # Redirect after successful registration
            except ValueError:
                messages.error(request, 'Invalid roll number format.')  # Display error message

    return render(request, 'register.html', {'course': course, 'students': students})

@login_required

def take_attendance(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    students = Student.objects.filter(course=course)

    if request.method == 'POST':
        voice_data = request.POST.get('voice_data', '')
        processed_attendance = process_voice_data(voice_data, students)

        # Save processed attendance data to your database
        for student_id, status in processed_attendance.items():
            student = students.get(student_id=student_id)  # Match by student ID
            # Store 1 for Present and 0 for Absent
            status_value = 1 if status == "Present" else 0  
            Attendance.objects.create(
                student=student,
                course=course,
                date=timezone.now().date(),
                status=status_value
            )

        return redirect('attendance_success')  # Redirect to a relevant view after saving

    return render(request, 'attendance.html', {'course': course, 'students': students})

def process_voice_data(voice_data, students):
    spoken_ids = extract_ids_from_voice(voice_data)
    attendance_data = {student.student_id: "Absent" for student in students}  # Default to Absent

    for student_id in attendance_data.keys():
        if student_id in spoken_ids:
            attendance_data[student_id] = "Present"  # Mark as Present if ID is found

    return attendance_data                                                             

def extract_ids_from_voice(voice_data):
    words = voice_data.lower().split() 
    ids = [] 

    for word in words:
        if word == "done":
            continue  
        try:
            # number = w2n.word_to_num(word) 
            number = int(word) 
            ids.append(number)  
        except ValueError:
            pass  

    return ids


def attendance_success(request):
    return render(request, 'attendance_success.html')
from .models import Course, Attendance
from django.db.models import Q

from django.shortcuts import render, get_object_or_404
from .models import Attendance, Course

from django.shortcuts import render, get_object_or_404
from .models import Course, Attendance

def show_attendance(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    selected_date = request.GET.get('attendance_date')
    attendance_records = []

    if selected_date:
        attendance_records = Attendance.objects.filter(course=course, date=selected_date)
        
        # Count present and absent students
        present_count = attendance_records.filter(status=1).count()
        absent_count = attendance_records.filter(status=0).count()

    context = {
        'course': course,
        'selected_date': selected_date,
        'attendance_records': attendance_records,
        'present_count': present_count if selected_date else 0,
        'absent_count': absent_count if selected_date else 0,
    }
    return render(request, 'show_attendance.html', context)



 








