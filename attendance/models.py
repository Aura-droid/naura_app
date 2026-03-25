from django.db import models
from django.utils import timezone
from django.conf import settings

class AttendanceRecord(models.Model):
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
        unique_together = ('school_class', 'date')

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
    date = models.DateField(default=timezone.now, unique=True)
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

    def __str__(self):
        return f"TOD Report - {self.date}"     