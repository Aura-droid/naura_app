import re
from django import forms
from django.contrib.auth.models import User
from .models import AttendanceRecord
from django.core.exceptions import ValidationError
from .models import DailyTODReport
from .models import ResultTemplate, ResultTemplateStatus, School, SchoolMembership, SchoolRole

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


class ResultTemplateForm(forms.ModelForm):
    class Meta:
        model = ResultTemplate
        fields = ['name', 'workbook']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Form One Midterm 2026'}),
            'workbook': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.status = ResultTemplateStatus.OPEN

    def clean_workbook(self):
        workbook = self.cleaned_data.get('workbook')
        if workbook and not workbook.name.lower().endswith('.xlsx'):
            raise ValidationError("Please upload an Excel workbook in .xlsx format.")
        return workbook

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.status = ResultTemplateStatus.OPEN
        if commit:
            instance.save()
        return instance


class ResultEntryBulkForm(forms.Form):
    submit_mode = forms.ChoiceField(
        choices=[('draft', 'Save Draft'), ('final', 'Submit Final')],
        widget=forms.HiddenInput(),
    )

    def clean(self):
        cleaned_data = super().clean()
        valid_codes = {'', '-', 'ABS', 'INC'}

        for key, value in self.data.items():
            if not key.startswith('score_'):
                continue

            score = (value or '').strip().upper()
            if score in valid_codes:
                continue
            try:
                numeric_score = float(score)
            except ValueError as exc:
                raise ValidationError(
                    f"{score} is not a valid mark. Use a number, ABS, INC, or leave blank."
                ) from exc

            if numeric_score < 0 or numeric_score > 100:
                raise ValidationError("Marks must be between 0 and 100.")

        return cleaned_data


class ResultTemplateStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=ResultTemplateStatus.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            'name',
            'portal_name',
            'slogan',
            'initiative_name',
            'initiative_short_name',
            'logo_with_background',
            'logo_without_background',
            'banner_image',
            'primary_color',
            'secondary_color',
            'accent_color',
            'support_phone',
            'support_email',
            'physical_address',
            'website_domain',
            'default_classes',
            'notes',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Example Secondary School'}),
            'portal_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Example Digital'}),
            'slogan': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Quality and Excellence'}),
            'initiative_name': forms.TextInput(attrs={'class': 'form-control'}),
            'initiative_short_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. N.D.I'}),
            'logo_with_background': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'logo_without_background': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'banner_image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'primary_color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#0d6efd'}),
            'secondary_color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#0d3b66'}),
            'accent_color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#ffc107'}),
            'support_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'support_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'physical_address': forms.TextInput(attrs={'class': 'form-control'}),
            'website_domain': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. school.example.com'}),
            'default_classes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'FORM 1 A, FORM 1 B, FORM 2 A'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SchoolAccountCreateForm(forms.Form):
    school = forms.ModelChoiceField(
        queryset=School.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    role = forms.ChoiceField(
        choices=SchoolRole.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    password1 = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    job_title = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    can_manage_school = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def __init__(self, *args, **kwargs):
        school_queryset = kwargs.pop('school_queryset', School.objects.filter(is_active=True))
        super().__init__(*args, **kwargs)
        self.fields['school'].queryset = school_queryset

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username=username).exists():
            raise ValidationError("That username already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError("The two passwords do not match.")
        return cleaned_data

    def save(self):
        school = self.cleaned_data['school']
        full_name = self.cleaned_data['full_name'].strip()
        first_name, _, last_name = full_name.partition(' ')
        user = User.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data.get('email', ''),
            password=self.cleaned_data['password1'],
            first_name=first_name,
            last_name=last_name,
        )
        membership = SchoolMembership.objects.create(
            school=school,
            user=user,
            role=self.cleaned_data['role'],
            job_title=self.cleaned_data.get('job_title', ''),
            can_manage_school=self.cleaned_data.get('can_manage_school', False),
        )
        return user, membership
