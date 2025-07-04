from django.db import models
from django.utils import timezone
from django.contrib.auth.hashers import make_password  # Import for password hashing

# Signup model for user registration
class Signup(models.Model):
    name = models.CharField(max_length=122)
    email = models.EmailField(max_length=122, unique=True)  # Ensure unique emails
    password = models.CharField(max_length=128)  # Store hashed password
    dept = models.CharField(max_length=100)
    date = models.DateField(default=timezone.now)  # Automatically set the date to now

    def save(self, *args, **kwargs):
        if self.pk is None:  # Only hash password on create
            self.password = make_password(self.password)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

# Course model for courses associated with users
class Course(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # Optional description
    user = models.ForeignKey(Signup, on_delete=models.CASCADE)  # Link to Signup model

    def __str__(self):
        return self.name

# Student model for registering students in courses
class Student(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE)  # Link to Course model
    name = models.CharField(max_length=255)
    roll_number = models.CharField(max_length=50, unique=True)  # Unique roll number
    student_id = models.IntegerField(null=True, unique=True)  # Make student_id nullable

    def save(self, *args, **kwargs):
        # Extract last two characters from roll_number and convert them to an integer for student_id
        if not self.student_id and self.roll_number[-2:].isdigit():  # Check if the last two are digits
            self.student_id = int(self.roll_number[-2:])  # Convert last two characters to an integer
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} (Roll No: {self.roll_number})"

# Attendance model to store student attendance data datewise
class Attendance(models.Model):
    STATUS_CHOICES = [
        (1, 'Present'),
        (0, 'Absent'),
    ]
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE,null=True)
    date = models.DateField(default=timezone.now)  # Automatically set to today's date
    status = models.IntegerField(choices=STATUS_CHOICES)  # Use choices for better readability

    def __str__(self):
        student_name = self.student.name if self.student else "No Student"
        course_name = self.course.name if self.course else "No Course"
        return f"{student_name} - {course_name} - {'Present' if self.status == 1 else 'Absent'}"






