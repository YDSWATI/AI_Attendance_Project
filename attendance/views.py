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

from django.contrib.auth.hashers import check_password

def user_login(request):
    if request.method == 'POST':
        email = request.POST['email']
        password = request.POST['password']

        try:
            user = Signup.objects.get(email=email)

            if check_password(password, user.password):

                request.session['user_id'] = user.id
                request.session['user_email'] = user.email
                request.session['user_name'] = user.name

                return redirect('home')

            else:
                messages.error(request, "Invalid password")

        except Signup.DoesNotExist:
            messages.error(request, "User does not exist")

    return render(request, 'login.html')


def home(request):

    user_id = request.session.get("user_id")

    if not user_id:
        return redirect("login")

    user_instance = Signup.objects.get(id=user_id)

    if request.method == "POST":
        course_name = request.POST.get('course_name')

        if course_name:
            Course.objects.create(
                name=course_name,
                user=user_instance
            )

            return redirect('home')

    courses = Course.objects.filter(user=user_instance)

    return render(
        request,
        'home.html',
        {
            'user': user_instance,
            'courses': courses
        }
    )
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
    request.session.flush()
    return redirect('login')

# @login_required
def register_students(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    students = Student.objects.filter(course=course)

    if request.method == 'POST':
        name = request.POST.get('name')
        roll_number = request.POST.get('roll_number')
        email = request.POST.get('email')

        if name and roll_number:
            try:
                # Extract the last two characters from roll_number and convert to an ID
                student_id = int(roll_number[-2:])  # Ensure roll_number has enough characters
                Student.objects.create(
                    course=course,
                    name=name,
                    roll_number=roll_number,
                    email=email,
                    student_id=student_id
                )
                return redirect('register', course_id=course.id)  # Redirect after successful registration
            except ValueError:
                messages.error(request, 'Invalid roll number format.')  # Display error message

    return render(request, 'register.html', {'course': course, 'students': students})

# @login_required

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

# In your views.py — add these imports at the top and the two functions below
# Make sure agent.py is inside your Django app folder

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

# Import your agent
from .agent import run_agent   # if agent.py is inside your app folder


# ─────────────────────────────────────────────
# View 1: Render the full-screen chat page
# ─────────────────────────────────────────────
# This just returns the ai_chat.html template.
# @login_required means only logged-in teachers can access it.

def ai_chat_page(request):

    if not request.session.get("user_id"):
        return redirect("login")

    return render(request, "chat.html")


# ─────────────────────────────────────────────
# View 2: Handle chat messages (AJAX endpoint)
# ─────────────────────────────────────────────
# The frontend sends: POST /ai-chat/  with JSON { "message": "..." }
# This view:
#   1. Gets the message
#   2. Gets the current teacher's user id
#   3. Calls run_agent() from agent.py
#   4. Returns the reply as JSON { "reply": "..." }


@csrf_exempt   # We handle CSRF via JS header in ai_chat.html
def ai_chat_api(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        # Parse the JSON body sent from frontend
        body    = json.loads(request.body)
        message = body.get("message", "").strip()

        if not message:
            return JsonResponse({"reply": "Please type a message."})

        # Get the logged-in teacher's id
        # request.session["user_id"] — because you use a custom Signup model
        # (not Django's built-in User), you stored id in session during login
        user_id = request.session.get("user_id")

        print("USER ID:", user_id)
        print("SESSION:", dict(request.session))

        if not user_id:
            return JsonResponse({"reply": "Session expired. Please login again."})

        reply = run_agent(message, user_id)


        return JsonResponse({"reply": reply})

    except Exception as e:
        # Always return something to frontend even if there's an error
        print(f"Agent error: {e}")   # shows in your terminal for debugging
        return JsonResponse({"reply": f"Something went wrong: {str(e)}"})


 








