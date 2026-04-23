from django.db import models
from django.utils import timezone
from django.conf import settings
from django.utils.text import slugify


class SchoolRole(models.TextChoices):
    HEADMASTER = "headmaster", "Headmaster"
    MANAGEMENT = "management", "Management"
    ACADEMIC_OFFICE = "academic_office", "Academic Office"
    TEACHER = "teacher", "Teacher"


class School(models.Model):
    initiative_name = models.CharField(max_length=150, default="Naura Digital Initiative")
    initiative_short_name = models.CharField(max_length=30, default="N.D.I")
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    portal_name = models.CharField(max_length=180, blank=True)
    slogan = models.CharField(max_length=255, blank=True)
    logo_with_background = models.FileField(upload_to="schools/logos/", blank=True)
    logo_without_background = models.FileField(upload_to="schools/logos/", blank=True)
    banner_image = models.FileField(upload_to="schools/banners/", blank=True)
    primary_color = models.CharField(max_length=7, default="#0d6efd")
    secondary_color = models.CharField(max_length=7, default="#0d3b66")
    accent_color = models.CharField(max_length=7, default="#ffc107")
    support_phone = models.CharField(max_length=50, blank=True)
    support_email = models.EmailField(blank=True)
    physical_address = models.CharField(max_length=255, blank=True)
    website_domain = models.CharField(max_length=255, blank=True)
    default_classes = models.TextField(blank=True, help_text="Optional comma-separated class list for this school.")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.portal_name.strip() if self.portal_name.strip() else f"{self.name} Digital"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name or self.portal_name or "school")
            slug = base_slug
            counter = 2
            while School.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class SchoolMembership(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="school_memberships",
    )
    role = models.CharField(max_length=30, choices=SchoolRole.choices, default=SchoolRole.TEACHER)
    job_title = models.CharField(max_length=120, blank=True)
    can_manage_school = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("school", "user")
        ordering = ["school__name", "role", "user__username"]

    def __str__(self):
        return f"{self.user.username} - {self.school.display_name} ({self.get_role_display()})"


class ResultTemplateStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    WITHDRAWN = "withdrawn", "Withdrawn"

class AttendanceRecord(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        null=True,
        blank=True,
    )
    school_class = models.CharField(max_length=50)
    date = models.DateField(default=timezone.now)

    total_boys_registered = models.PositiveIntegerField(default=0)
    total_girls_registered = models.PositiveIntegerField(default=0)
    
    # Presentees
    present_boys = models.PositiveIntegerField(default=0)
    present_girls = models.PositiveIntegerField(default=0)
    
    # Permitted (With Permission)
    permitted_boys = models.PositiveIntegerField(default=0)
    permitted_girls = models.PositiveIntegerField(default=0)
    permitted_names = models.TextField(blank=True, help_text="Names separated by commas")
    
    # Truants (No Permission)
    truant_boys = models.PositiveIntegerField(default=0)
    truant_girls = models.PositiveIntegerField(default=0)
    truant_names = models.TextField(blank=True, help_text="Names separated by commas")

    class Meta:
        # This belongs HERE because AttendanceRecord HAS a school_class field
        unique_together = ('school', 'school_class', 'date')

    @property
    def total_present(self):
        return self.present_boys + self.present_girls

    @property
    def total_permitted(self):
        return self.permitted_boys + self.permitted_girls

    @property
    def total_absent(self):
        return self.truant_boys + self.truant_girls

    @property
    def total_registered(self):
        return self.total_boys_registered + self.total_girls_registered

    @property
    def attendance_percentage(self):
        # We check total_registered once to avoid division by zero
        total = self.total_registered
        if total > 0:
            return round((self.total_present / total) * 100, 1)
        return 0.0

    def __str__(self):
        return f"{self.school_class} - {self.date}"
    
class DailyTODReport(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="tod_reports",
        null=True,
        blank=True,
    )
    date = models.DateField(default=timezone.now)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    teacher_name = models.CharField(
        max_length=100, 
        null=True,   # Allows the database to have empty values
        blank=True,  # Allows the form to be submitted empty if needed
        verbose_name="Teacher's Full Name",
        help_text="Enter the name of the teacher submitting this report (for record-keeping and accountability purposes)."
    )
    tod_names = models.CharField(max_length=255, help_text="Names of the 2-3 teachers on duty")
    
    # Morning
    arrival_time = models.TimeField()
    compound_cleanliness = models.TextField(help_text="Which class? Any challenges?")
    morning_sessions = models.TextField(help_text="Prep classes status/challenges")
    
    # Afternoon
    lunch_details = models.TextField(help_text="Menu, preparation, and serving status")
    
    # Evening & Afterschool
    evening_remedial = models.TextField(help_text="Evening classes and remedial status")
    departure_time = models.TimeField()
    
    # Facility & Overall
    maintenance_notes = models.TextField(help_text="Repairs, water, electricity, etc.")
    overall_comments = models.TextField(blank=True)

    class Meta:
        ordering = ['-date']
        unique_together = ('school', 'date')

    def __str__(self):
        return f"TOD Report - {self.date}" 

    # models.py
class NotificationLog(models.Model):
          school = models.ForeignKey(
              School,
              on_delete=models.CASCADE,
              related_name="notification_logs",
              null=True,
              blank=True,
          )
          date = models.DateField(auto_now_add=True)
          sent = models.BooleanField(default=False)


class ResultTemplate(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="result_templates",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)
    workbook = models.FileField(upload_to="result_templates/")
    status = models.CharField(
        max_length=20,
        choices=ResultTemplateStatus.choices,
        default=ResultTemplateStatus.OPEN,
    )
    sheet_name = models.CharField(max_length=120, blank=True)
    header_row = models.PositiveIntegerField(default=13)
    first_student_row = models.PositiveIntegerField(default=14)
    last_student_row = models.PositiveIntegerField(default=14)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_result_templates",
    )
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def can_edit(self):
        return self.status == ResultTemplateStatus.OPEN

    def rebuild_structure(self):
        from .excel_utils import rebuild_template_structure

        rebuild_template_structure(self)


class ResultTemplateSubject(models.Model):
    template = models.ForeignKey(
        ResultTemplate,
        on_delete=models.CASCADE,
        related_name="subjects",
    )
    name = models.CharField(max_length=100)
    column_letter = models.CharField(max_length=5)
    column_index = models.PositiveIntegerField()
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "column_index", "name"]
        unique_together = ("template", "column_index")

    def __str__(self):
        return f"{self.template.name} - {self.name}"


class ResultStudent(models.Model):
    template = models.ForeignKey(
        ResultTemplate,
        on_delete=models.CASCADE,
        related_name="students",
    )
    row_number = models.PositiveIntegerField()
    centre_no = models.CharField(max_length=20, blank=True)
    candidate_no = models.CharField(max_length=30)
    student_name = models.CharField(max_length=150)
    sex = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["row_number", "candidate_no", "student_name"]
        unique_together = ("template", "row_number")

    def __str__(self):
        return f"{self.candidate_no} - {self.student_name}"


class ResultEntry(models.Model):
    template = models.ForeignKey(
        ResultTemplate,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    subject = models.ForeignKey(
        ResultTemplateSubject,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    student = models.ForeignKey(
        ResultStudent,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    raw_score = models.CharField(max_length=20, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_result_entries",
    )
    is_final = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student__row_number"]
        unique_together = ("template", "subject", "student")

    def __str__(self):
        return f"{self.subject.name} - {self.student.student_name}"
