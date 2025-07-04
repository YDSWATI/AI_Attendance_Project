from django.contrib import admin
from .models import Signup, Course, Student, Attendance

# Inline class for Student within Course
class StudentInline(admin.TabularInline):  # You can also use admin.StackedInline
    model = Student
    extra = 1  # Number of empty forms for adding new students
    fields = ('name', 'roll_number', 'student_id')  # Updated to include 'student_id'
    readonly_fields = ('student_id',)  # Make 'student_id' read-only to prevent accidental edits

# Customizing the Course admin interface with StudentInline
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'user')  # Fields to display in the admin list
    search_fields = ('name',)  # Fields to search in the admin
    list_filter = ('user',)  # Filter options in the admin
    inlines = [StudentInline]  # Add the Student inline model

# Customizing the Student admin interface
class StudentAdmin(admin.ModelAdmin):
    list_display = ('name', 'roll_number', 'student_id', 'course')  # Updated to display 'student_id'
    search_fields = ('name', 'roll_number')  # Fields to search in the admin
    list_filter = ('course',)  # Filter options in the admin
    readonly_fields = ('student_id',)  # Make 'student_id' read-only in admin

# Customizing the Signup admin interface
class SignupAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'dept', 'date')  # Fields to display in the admin list
    search_fields = ('name', 'email')  # Fields to search in the admin
    list_filter = ('dept',)  # Filter options in the admin

# Customizing the Attendance admin interface
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'date', 'status_display')  # Updated to include 'course'
    search_fields = ('student__name', 'student__roll_number')  # Search by student's name or roll number
    list_filter = ('date', 'status', 'course')  # Updated to filter by 'course'
    readonly_fields = ('student', 'course')  # Make student and course fields read-only in admin
    list_per_page = 25  # Display 25 records per page

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs  # Superuser can see all attendance records
        else:
            return qs.filter(course__user=request.user)

    def status_display(self, obj):
        return 'Present' if obj.status == 1 else 'Absent'
    status_display.short_description = 'Attendance Status'

    def mark_present(self, request, queryset):
        queryset.update(status=1)  # Set status to 1 for present
    mark_present.short_description = "Mark selected as Present"

    actions = [mark_present]  # Add custom actions to the admin


# Registering the models with the custom admin interfaces
admin.site.register(Signup, SignupAdmin)
admin.site.register(Course, CourseAdmin)
admin.site.register(Student, StudentAdmin)  # Registering Student separately
admin.site.register(Attendance, AttendanceAdmin)
 # Registering Attendance model




# from django.contrib import admin
# from .models import Signup, Course, Student, Attendance

# class CourseInline(admin.TabularInline):
#     model = Course
#     extra = 1  # Number of empty forms to display for adding new courses

# class StudentInline(admin.TabularInline):
#     model = Student
#     extra = 1  # Number of empty forms to display for adding new students



# class CourseAdmin(admin.ModelAdmin):
#     inlines = [StudentInline, AttendanceInline]  # Add the inlines to the Course admin
#     list_display = ('name', 'user')  # Display relevant fields in the admin list view

# class SignupAdmin(admin.ModelAdmin):
#     inlines = [CourseInline]  # Add the inline to the Signup admin
#     list_display = ('name', 'email', 'dept', 'date')  # Display relevant fields in the admin list view

# # Register your models here
# admin.site.register(Signup, SignupAdmin)
# admin.site.register(Course, CourseAdmin)  # Use the custom CourseAdmin class
# admin.site.register(Student)  # Register the Student model
# admin.site.register(Attendance)  # Register the Attendance model

