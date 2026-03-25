from django.contrib import admin
from .models import AttendanceRecord

# This decorator handles the registration. 
# Do NOT use admin.site.register() at the bottom if you use this.
@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        'school_class', 
        'date', 
        'total_present', 
        'total_permitted', 
        'total_absent'
    )
    list_filter = ('date', 'school_class')
    search_fields = ('school_class', 'truant_names', 'permitted_names')
    
    # This makes the view cleaner by grouping fields in the admin edit page
    fieldsets = (
        ('Class Info', {'fields': ('school_class', 'date')}),
        ('Presentees', {'fields': ('present_boys', 'present_girls')}),
        ('With Permission', {'fields': ('permitted_boys', 'permitted_girls', 'permitted_names')}),
        ('Truants', {'fields': ('truant_boys', 'truant_girls', 'truant_names')}),
    )