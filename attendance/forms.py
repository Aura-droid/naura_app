import re
from django import forms
from .models import AttendanceRecord
from django.core.exceptions import ValidationError
from .models import DailyTODReport

class AttendanceForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = [
            'school_class', 
            'total_boys_registered', 'total_girls_registered', 
            'present_boys', 'present_girls', 
            'permitted_boys', 'permitted_girls', 
            'truant_boys', 'truant_girls',
            'permitted_names', 'truant_names'
        ]
        
        widgets = {
    'school_class': forms.TextInput(attrs={
        'class': 'form-control',
        'id': 'class-select',  # We'll use this ID for the JavaScript
        'placeholder': 'Search or select a class...',
        'autocomplete': 'off',
    }),

            'permitted_names': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'truant_names': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            # Added 'form-control' and min=0 to all number fields for a consistent UI
            'total_boys_registered': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'total_girls_registered': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'present_boys': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'present_girls': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'permitted_boys': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'permitted_girls': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'truant_boys': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'truant_girls': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # We make truant fields optional so JavaScript can fill them without validation errors
        self.fields['truant_boys'].required = False
        self.fields['truant_girls'].required = False

    def clean_school_class(self):
        data = self.cleaned_data.get('school_class')
        if not data:
            return data

        # Force Uppercase and remove double spaces
        data = " ".join(data.upper().split())

        # Fix "ONE" to "1" etc.
        replacements = {
            r"\bONE\b": "1", r"\bTWO\b": "2", r"\bTHREE\b": "3",
            r"\bFOUR\b": "4", r"\bFIVE\b": "5", r"\bSIX\b": "6"
        }
        for pattern, num in replacements.items():
            data = re.sub(pattern, num, data)

        # Final formatting to "FORM 1 A"
        match = re.match(r"(FORM)\s*(\d)\s*([A-Z])", data)
        if match:
            data = f"FORM {match.group(2)} {match.group(3)}"

        return data

    def clean(self):
        cleaned_data = super().clean()
        
        # Get values, defaulting to 0 if empty
        reg_b = cleaned_data.get('total_boys_registered') or 0
        reg_g = cleaned_data.get('total_girls_registered') or 0
        pres_b = cleaned_data.get('present_boys') or 0
        pres_g = cleaned_data.get('present_girls') or 0
        perm_b = cleaned_data.get('permitted_boys') or 0
        perm_g = cleaned_data.get('permitted_girls') or 0
        tru_b = cleaned_data.get('truant_boys') or 0
        tru_g = cleaned_data.get('truant_girls') or 0

        # Server-side validation to ensure data integrity
        if (pres_b + perm_b + tru_b) != reg_b:
            raise ValidationError(f"Math Error (Boys): Total registered is {reg_b}, but sum of others is {pres_b + perm_b + tru_b}.")

        if (pres_g + perm_g + tru_g) != reg_g:
            raise ValidationError(f"Math Error (Girls): Total registered is {reg_g}, but sum of others is {pres_g + perm_g + tru_g}.")

        return cleaned_data
    
class TODReportForm(forms.ModelForm):
    class Meta:
        model = DailyTODReport
        fields = '__all__'
        exclude = ['submitted_by', 'date']
        widgets = {
            'arrival_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'departure_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'tod_names': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Mr. Linus, Ms. Dorcas'}),
            # Using 3 rows for text areas to keep them sleek
            'compound_cleanliness': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'morning_sessions': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'lunch_details': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'evening_remedial': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'maintenance_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'overall_comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'teacher_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Mr. Cedes Japhet'}),
        }    