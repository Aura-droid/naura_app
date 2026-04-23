import os
import json
import re
import csv
from datetime import timedelta
import ssl
import httpx
from datetime import datetime
from .utils import send_staff_reminder
from .models import NotificationLog

# --- AI & Environment ---
from google import genai 
from dotenv import load_dotenv

# --- Django Core ---
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Q, Sum
from django.utils import timezone
from django.http import FileResponse, HttpResponse, JsonResponse
from django.template.loader import get_template
from django.contrib.auth.decorators import user_passes_test, login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.db.models.functions import Upper, TruncMonth
from django.core.cache import cache
from django.contrib.auth.models import User
from django.contrib.auth.models import Group
from django.db import models
from django.conf import settings
from django.http import Http404

# --- PDF Generation ---
from xhtml2pdf import pisa

# --- Project Models & Forms ---
from .excel_utils import build_export_workbook, export_filename_for_template, load_openpyxl
from .forms import (
    AttendanceForm,
    ResultEntryBulkForm,
    SchoolAccountCreateForm,
    SchoolForm,
    ResultTemplateForm,
    ResultTemplateStatusForm,
    TODReportForm,
)
from .models import (
    AttendanceRecord,
    DailyTODReport,
    ResultEntry,
    School,
    SchoolMembership,
    SchoolRole,
    ResultTemplate,
    ResultTemplateStatus,
    ResultTemplateSubject,
)

load_dotenv()

ROLE_TO_GROUPS = {
    SchoolRole.HEADMASTER: ['Management'],
    SchoolRole.MANAGEMENT: ['Management'],
    SchoolRole.ACADEMIC_OFFICE: ['Academic Office'],
    SchoolRole.TEACHER: ['Teachers'],
}

# --- PERMISSION HELPERS ---

def is_management(user):
    """Checks if the user belongs to the Management group or is a Superuser."""
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Management').exists())

def is_academic_office(user):
    """Checks if the user belongs to the Academic Office group or is a Superuser."""
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Academic Office').exists())

def is_results_office(user):
    """Checks if the user can manage academic result templates."""
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=['Management', 'Academic Office']).exists()
    )

def is_teacher_or_mgmt(user):
    """Checks if the user is a Teacher, Management, or Superuser."""
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=['Teachers', 'Management']).exists())


def is_platform_admin(user):
    return user.is_authenticated and user.is_superuser


def ensure_portal_groups():
    for group_name in ['Management', 'Academic Office', 'Teachers']:
        Group.objects.get_or_create(name=group_name)


def assign_membership_groups(user, role):
    ensure_portal_groups()
    user.groups.clear()
    for group_name in ROLE_TO_GROUPS.get(role, []):
        user.groups.add(Group.objects.get(name=group_name))


def get_user_schools(user):
    if not user.is_authenticated:
        return School.objects.none()
    if user.is_superuser:
        return School.objects.filter(is_active=True).order_by('name')
    return School.objects.filter(is_active=True, memberships__user=user).distinct().order_by('name')


def get_active_school(request, school_slug=None, allow_none=True):
    schools = get_user_schools(request.user)
    if not schools.exists():
        if allow_none:
            return None
        raise Http404("No active school is available for this user.")

    if school_slug:
        school = schools.filter(slug=school_slug).first()
        if school:
            request.session['active_school_id'] = school.id
            return school
        if allow_none:
            return None
        raise Http404("School not found.")

    session_school_id = request.session.get('active_school_id')
    if session_school_id:
        school = schools.filter(id=session_school_id).first()
        if school:
            return school

    school = schools.first()
    request.session['active_school_id'] = school.id
    return school


def require_active_school(request, school_slug=None):
    return get_active_school(request, school_slug=school_slug, allow_none=False)


def build_school_url_name(view_name):
    if view_name.startswith('school_'):
        return view_name
    return f"school_{view_name}"


def school_reverse(view_name, school, **kwargs):
    from django.urls import reverse

    if not school:
        return None
    return reverse(build_school_url_name(view_name), kwargs={'school_slug': school.slug, **kwargs})


def filter_for_school(queryset, school, field_name='school'):
    if not school:
        return queryset
    return queryset.filter(**{field_name: school})


def resolve_school_logo_path(school):
    if school:
        for field_name in ['logo_with_background', 'logo_without_background']:
            field = getattr(school, field_name, None)
            if field:
                try:
                    if field.path and os.path.exists(field.path):
                        return field.path
                except (ValueError, NotImplementedError):
                    pass
    return os.path.join(settings.BASE_DIR, 'static', 'assets', 'img', 'mwandeticon.jpg')


def build_subject_progress_rows(template):
    total_students = template.students.count()
    entry_queryset = ResultEntry.objects.filter(template=template).select_related('submitted_by')
    entry_summary = {
        row['subject_id']: row
        for row in (
            entry_queryset.values('subject_id')
            .annotate(
                entered_count=Count('id', filter=~Q(raw_score='')),
                final_count=Count('id', filter=Q(is_final=True)),
            )
        )
    }
    latest_entries = {}
    for entry in entry_queryset.order_by('subject_id', '-updated_at'):
        latest_entries.setdefault(entry.subject_id, entry)

    subject_rows = []
    for subject in template.subjects.all():
        summary = entry_summary.get(subject.id, {})
        entered_count = summary.get('entered_count', 0)
        final_count = summary.get('final_count', 0)
        latest_entry = latest_entries.get(subject.id)

        if total_students and final_count >= total_students:
            status_label = "Submitted"
            status_class = "success"
        elif entered_count > 0:
            status_label = "In Progress"
            status_class = "warning"
        else:
            status_label = "Pending"
            status_class = "danger"

        subject_rows.append(
            {
                'subject': subject,
                'entered_count': entered_count,
                'final_count': final_count,
                'total_students': total_students,
                'status_label': status_label,
                'status_class': status_class,
                'pending_count': max(total_students - final_count, 0),
                'latest_updated_at': latest_entry.updated_at if latest_entry else None,
                'latest_updated_by': (
                    latest_entry.submitted_by.get_full_name() or latest_entry.submitted_by.username
                )
                if latest_entry and latest_entry.submitted_by
                else "",
            }
        )

    return subject_rows


def filter_subject_rows(subject_rows, status_filter):
    if status_filter == 'pending':
        return [row for row in subject_rows if row['status_label'] == 'Pending']
    if status_filter == 'in_progress':
        return [row for row in subject_rows if row['status_label'] == 'In Progress']
    if status_filter == 'submitted':
        return [row for row in subject_rows if row['status_label'] == 'Submitted']
    return subject_rows


def build_single_subject_progress(template, subject):
    total_students = template.students.count()
    subject_entries = ResultEntry.objects.filter(template=template, subject=subject)
    summary = subject_entries.aggregate(
        entered_count=Count('id', filter=~Q(raw_score='')),
        final_count=Count('id', filter=Q(is_final=True)),
    )
    latest_entry = subject_entries.select_related('submitted_by').order_by('-updated_at').first()

    entered_count = summary.get('entered_count', 0) or 0
    final_count = summary.get('final_count', 0) or 0

    if total_students and final_count >= total_students:
        status_label = "Submitted"
        status_class = "success"
    elif entered_count > 0:
        status_label = "In Progress"
        status_class = "warning"
    else:
        status_label = "Pending"
        status_class = "danger"

    return {
        'subject': subject,
        'entered_count': entered_count,
        'final_count': final_count,
        'total_students': total_students,
        'status_label': status_label,
        'status_class': status_class,
        'pending_count': max(total_students - final_count, 0),
        'latest_updated_at': latest_entry.updated_at if latest_entry else None,
        'latest_updated_by': (
            latest_entry.submitted_by.get_full_name() or latest_entry.submitted_by.username
        )
        if latest_entry and latest_entry.submitted_by
        else "",
    }


def template_workbook_is_available(template):
    workbook_field = getattr(template, 'workbook', None)
    if not workbook_field:
        return False

    try:
        workbook_path = workbook_field.path
    except (ValueError, NotImplementedError):
        return False

    return bool(workbook_path and os.path.exists(workbook_path))


def redirect_missing_template_workbook(request, template, fallback_url_name, **fallback_kwargs):
    messages.error(
        request,
        f'The original workbook file for "{template.name}" is no longer available. '
        'Please ask Academic Office to re-upload the template file.',
    )
    return redirect(fallback_url_name, **fallback_kwargs)


def extract_template_grade_rules(template):
    if not template_workbook_is_available(template):
        return {}

    cache_key = f"template_grade_rules:{template.id}:{template.updated_at.timestamp()}:{template.workbook.name}"
    cached_rules = cache.get(cache_key)
    if cached_rules is not None:
        return cached_rules

    load_workbook, _ = load_openpyxl()
    workbook = load_workbook(template.workbook.path, data_only=False)
    worksheet = workbook[template.sheet_name or workbook.sheetnames[0]]
    score_column_rules = {}
    lookup_pattern = re.compile(r"LOOKUP\(([A-Z]+)\d+,\$([A-Z]+)\$\d+:\$([A-Z]+)\$\d+\)")

    for column_index in range(1, worksheet.max_column + 1):
        cell = worksheet.cell(row=template.first_student_row, column=column_index)
        formula = cell.value
        if not isinstance(formula, str) or "LOOKUP(" not in formula.upper():
            continue

        match = lookup_pattern.search(formula.upper())
        if not match:
            continue

        source_column, threshold_column, label_column = match.groups()
        rules = []
        row_number = 1
        while row_number <= worksheet.max_row:
            threshold = worksheet[f"{threshold_column}{row_number}"].value
            label = worksheet[f"{label_column}{row_number}"].value
            row_number += 1

            if threshold in (None, "", "-") or label in (None, "", "-"):
                continue

            try:
                numeric_threshold = float(threshold)
            except (TypeError, ValueError):
                continue

            rules.append((numeric_threshold, str(label).strip().upper()))

        if rules:
            score_column_rules[source_column] = sorted(rules, key=lambda item: item[0])

    cache.set(cache_key, score_column_rules, 60 * 60)
    return score_column_rules


def grade_from_score(raw_score, rules=None):
    score = (raw_score or '').strip().upper()
    if not score:
        return 'BLANK'
    if score in {'ABS', 'INC'}:
        return score
    try:
        numeric_score = float(score)
    except ValueError:
        return 'OTHER'

    if rules:
        selected_label = None
        for threshold, label in rules:
            if numeric_score >= threshold:
                selected_label = label
            else:
                break
        if selected_label:
            return selected_label

    # Fallback only if no template rules could be read.
    if numeric_score >= 81:
        return 'A'
    if numeric_score >= 61:
        return 'B'
    if numeric_score >= 41:
        return 'C'
    if numeric_score >= 21:
        return 'D'
    return 'F'


ANALYSIS_CATEGORIES = ['A', 'B', 'C', 'D', 'F', 'ABS', 'INC', 'BLANK']
PASSING_CATEGORIES = ['A', 'B', 'C', 'D']
FAILING_CATEGORIES = ['F']


def build_pass_fail_summary(counts):
    summary = {}
    for label, data in counts.items():
        graded_total = sum(data[category] for category in PASSING_CATEGORIES + FAILING_CATEGORIES)
        pass_count = sum(data[category] for category in PASSING_CATEGORIES)
        fail_count = sum(data[category] for category in FAILING_CATEGORIES)
        pass_pct = (pass_count / graded_total * 100) if graded_total else 0
        fail_pct = (fail_count / graded_total * 100) if graded_total else 0
        summary[label] = {
            'graded_total': graded_total,
            'pass_count': pass_count,
            'pass_pct': round(pass_pct, 1),
            'fail_count': fail_count,
            'fail_pct': round(fail_pct, 1),
        }
    return summary


def build_single_subject_analysis(template, subject, final_only=False):
    grade_rules_by_column = extract_template_grade_rules(template)
    counts = {
        'M': {category: 0 for category in ANALYSIS_CATEGORIES},
        'F': {category: 0 for category in ANALYSIS_CATEGORIES},
        'TOTAL': {category: 0 for category in ANALYSIS_CATEGORIES},
    }

    entries = ResultEntry.objects.filter(template=template, subject=subject).select_related('student')
    if final_only:
        entries = entries.filter(is_final=True)
    entries = entries.order_by('student__candidate_no')

    for entry in entries:
        gender = (entry.student.sex or '').strip().upper()
        category = grade_from_score(
            entry.raw_score,
            rules=grade_rules_by_column.get(entry.subject.column_letter),
        )
        if category not in ANALYSIS_CATEGORIES:
            continue
        if gender in {'M', 'F'}:
            counts[gender][category] += 1
        counts['TOTAL'][category] += 1

    return {
        'subject': subject,
        'male': counts['M'],
        'female': counts['F'],
        'total': counts['TOTAL'],
        'summary': build_pass_fail_summary(counts),
    }


def build_subject_analysis_rows(template):
    submitted_subjects = [
        row['subject']
        for row in build_subject_progress_rows(template)
        if row['status_label'] == 'Submitted'
    ]
    analysis_rows = []

    for subject in submitted_subjects:
        analysis_rows.append(build_single_subject_analysis(template, subject, final_only=True))

    return analysis_rows


def analysis_has_data(analysis_row):
    return any(analysis_row['total'][category] for category in ANALYSIS_CATEGORIES)


def subject_has_saved_marks(template, subject):
    return ResultEntry.objects.filter(template=template, subject=subject).exclude(raw_score='').exists()


def write_analysis_rows_csv(response, analysis_rows):
    writer = csv.writer(response)
    writer.writerow(['Subject', 'Gender', *ANALYSIS_CATEGORIES])
    gender_rows = (
        ('Male', 'male'),
        ('Female', 'female'),
        ('Total', 'total'),
    )
    for row in analysis_rows:
        for gender_label, key in gender_rows:
            writer.writerow(
                [
                    row['subject'].name,
                    gender_label,
                    *[row[key][category] for category in ANALYSIS_CATEGORIES],
                ]
            )


def render_analysis_pdf_response(request, template, analysis_rows, title, subtitle, filename):
    active_school = getattr(template, 'school', None) or get_active_school(request)
    logo_path = resolve_school_logo_path(active_school)
    context = {
        'template_obj': template,
        'analysis_rows': analysis_rows,
        'analysis_categories': ANALYSIS_CATEGORIES,
        'report_title': title,
        'report_subtitle': subtitle,
        'logo_path': logo_path,
        'user': request.user,
        'school_name': active_school.display_name if active_school else 'School Digital',
    }
    html = get_template('attendance/result_analysis_pdf.html').render(context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    pisa.CreatePDF(html, dest=response)
    return response

# --- HELPER: AI INSIGHTS ---

def get_ai_insights(monthly_data, tod_notes, school_name="School Digital"):
    """Fetches and cleans insights for a professional dashboard UI."""
    try:
        # THE FIX: Initialize the Gemini client properly
        # We handle the SSL/Timeout issues by passing a custom config if needed, 
        # but don't overwrite the 'client' variable with httpx.
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        
        prompt = f"""
        Analyze school data for {school_name}:
        Attendance: {monthly_data}
        TOD Notes: {tod_notes}
        
        STRICT FORMATTING:
        - Use "Summary:" as a heading.
        - Use a bulleted list for trends and recommendations.
        - Under 60 words. No conversational filler.
        """
        
        # Use gemini-2.5-flash for MD dashboard insights
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = response.text

        # Formatting for UI
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = text.replace('* ', '• ')
        return text.strip()
        
    except Exception as e:
        # This catches the SSL: UNEXPECTED_EOF errors gracefully
        return f"AI insights unavailable. ({str(e)})"

# --- LOGIN & REDIRECT LOGIC ---

def home_redirect(request):
    if request.user.is_authenticated:
        return login_success_redirect(request)
    return redirect('school_directory')


def logout_user(request):
    logout(request)
    return redirect('school_directory')


def school_directory(request):
    schools = School.objects.filter(is_active=True).order_by('name')
    return render(
        request,
        'attendance/school_directory.html',
        {
            'schools': schools,
            'use_platform_branding': True,
        },
    )


def school_login(request, school_slug):
    school = get_object_or_404(School, slug=school_slug, is_active=True)
    form = AuthenticationForm(request, data=request.POST or None)
    form.fields['username'].widget.attrs.update({'class': 'form-control rounded-pill', 'autofocus': True})
    form.fields['password'].widget.attrs.update({'class': 'form-control rounded-pill'})

    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data.get('username')
        password = form.cleaned_data.get('password')
        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, "Invalid username or password.")
        elif not user.is_superuser and not SchoolMembership.objects.filter(user=user, school=school).exists():
            messages.error(request, f'This account is not assigned to {school.display_name}.')
        else:
            login(request, user)
            request.session['active_school_id'] = school.id
            return login_success_redirect(request)

    return render(
        request,
        'attendance/school_login.html',
        {
            'form': form,
            'active_school': school,
            'school_display_name': school.display_name,
            'platform_name': school.initiative_name,
            'platform_short_name': school.initiative_short_name,
        },
    )


def web_manifest(request):
    active_school = get_active_school(request)
    icon_src = (
        active_school.logo_without_background.url
        if active_school and active_school.logo_without_background
        else f"{settings.STATIC_URL}assets/img/md-app-icon.png"
    )
    manifest = {
        "name": active_school.display_name if active_school else "School Digital",
        "id": "/?app=md",
        "short_name": active_school.display_name if active_school else "NDI Portal",
        "start_url": "/?source=md-pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f8f9fa",
        "theme_color": active_school.primary_color if active_school else "#0d6efd",
        "description": f"{active_school.display_name if active_school else 'School Digital'} staff portal for attendance, TOD reports, and academic templates.",
        "icons": [
            {
                "src": icon_src,
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": icon_src,
                "sizes": "512x512",
                "type": "image/png",
            },
            {
                "src": icon_src,
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {
                "name": "Teacher Hub",
                "url": "/hub/",
                "description": "Open the teacher hub quickly.",
            },
            {
                "name": "Academic Office",
                "url": "/results/dashboard/",
                "description": "Open the academic office dashboard.",
            },
            {
                "name": "Management",
                "url": "/dashboard/",
                "description": "Open the management dashboard.",
            },
        ],
    }
    return HttpResponse(json.dumps(manifest), content_type='application/manifest+json')


def offline_page(request):
    return render(request, 'attendance/offline.html')


def service_worker(request):
    offline_url = "/offline/"
    active_school = get_active_school(request)
    icon_path = (
        active_school.logo_without_background.url
        if active_school and active_school.logo_without_background
        else f"{settings.STATIC_URL}assets/img/md-app-icon.png"
    )
    icon_192 = icon_path
    icon_512 = icon_path
    js = f"""
self.addEventListener('install', event => {{
  event.waitUntil(
    caches.open('md-portal-v1').then(cache => cache.addAll([
      '{offline_url}',
      '{icon_192}',
      '{icon_512}'
    ]))
  );
  self.skipWaiting();
}});

self.addEventListener('activate', event => {{
  event.waitUntil(self.clients.claim());
}});

self.addEventListener('fetch', event => {{
  if (event.request.method !== 'GET') {{
    return;
  }}

  if (event.request.mode === 'navigate') {{
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match('{offline_url}')
      )
    );
    return;
  }}

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
}});
"""
    return HttpResponse(js, content_type='application/javascript')

def login_success_redirect(request):
    """Route users to the right workspace after login."""
    if not request.user.is_authenticated:
        return HttpResponse("DEBUG ERROR: You are not even logged in.")

    # Get a list of all groups this user actually has
    user_groups = list(request.user.groups.values_list('name', flat=True))
    active_school = get_active_school(request)
    
    if request.user.is_superuser:
        return redirect('platform_school_dashboard')

    if 'Management' in user_groups:
        return redirect(school_reverse('master_dashboard', active_school) or 'master_dashboard')

    elif 'Academic Office' in user_groups:
        return redirect(school_reverse('result_template_dashboard', active_school) or 'result_template_dashboard')
    
    elif 'Teachers' in user_groups:
        return redirect(school_reverse('teacher_hub', active_school) or 'teacher_hub')
    
    else:
        # If they reach here, they are logged in but in the WRONG group
        return HttpResponse(f"""
            <h1>Group Mismatch Error</h1>
            <p><strong>User:</strong> {request.user.username}</p>
            <p><strong>Groups found in database:</strong> {user_groups}</p>
            <p><strong>Required:</strong> 'Teachers', 'Academic Office', or 'Management' (Case-Sensitive!)</p>
            <a href="/admin/">Go to Admin to fix Groups</a>
        """)


@user_passes_test(is_platform_admin)
def platform_school_dashboard(request):
    schools = School.objects.prefetch_related('memberships__user').order_by('name')
    school_form = SchoolForm(prefix='school')
    account_form = SchoolAccountCreateForm(prefix='account', school_queryset=schools.filter(is_active=True))

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_school':
            school_form = SchoolForm(request.POST, request.FILES, prefix='school')
            if school_form.is_valid():
                school = school_form.save()
                request.session['active_school_id'] = school.id
                messages.success(request, f'{school.display_name} has been created and is ready for setup.')
                return redirect('platform_school_dashboard')
        elif action == 'create_account':
            account_form = SchoolAccountCreateForm(
                request.POST,
                prefix='account',
                school_queryset=schools.filter(is_active=True),
            )
            if account_form.is_valid():
                user, membership = account_form.save()
                assign_membership_groups(user, membership.role)
                messages.success(
                    request,
                    f'Created {membership.get_role_display().lower()} account "{user.username}" for {membership.school.display_name}.',
                )
                return redirect('platform_school_dashboard')

    selected_school = get_active_school(request)
    school_memberships = (
        SchoolMembership.objects.select_related('school', 'user')
        .filter(school=selected_school)
        .order_by('role', 'user__username')
        if selected_school
        else SchoolMembership.objects.none()
    )
    school_stats = []
    for school in schools:
        school_stats.append(
            {
                'school': school,
                'member_count': school.memberships.count(),
                'template_count': school.result_templates.count(),
                'attendance_count': school.attendance_records.count(),
            }
        )

    return render(
        request,
        'attendance/platform_school_dashboard.html',
        {
            'school_form': school_form,
            'account_form': account_form,
            'school_stats': school_stats,
            'selected_school': selected_school,
            'school_memberships': school_memberships,
            'use_platform_branding': True,
        },
    )


@user_passes_test(is_platform_admin)
def switch_active_school(request, school_id):
    school = get_object_or_404(School, pk=school_id, is_active=True)
    request.session['active_school_id'] = school.id
    messages.success(request, f'Active school switched to {school.display_name}.')
    next_url = request.GET.get('next')
    if next_url:
        return redirect(next_url)
    return redirect(school_reverse('teacher_hub', school) or 'platform_school_dashboard')

@login_required
def teacher_hub(request, school_slug=None):
    """Landing page for teachers to choose between Attendance or TOD tasks."""
    active_school = require_active_school(request, school_slug)
    open_templates = (
        filter_for_school(ResultTemplate.objects, active_school)
        .filter(status=ResultTemplateStatus.OPEN)
        .prefetch_related('subjects')
        .order_by('-created_at')
    )
    pending_templates = []
    for template in open_templates:
        subject_rows = build_subject_progress_rows(template)
        pending_count = sum(1 for row in subject_rows if row['status_label'] != 'Submitted')
        if pending_count:
            pending_templates.append({'template': template, 'pending_count': pending_count})
    return render(
        request,
        'attendance/teacher_hub.html',
        {
            'open_result_templates': open_templates,
            'pending_result_templates': pending_templates,
            'active_school': active_school,
        },
    )


@user_passes_test(is_teacher_or_mgmt)
def teacher_result_hub(request, school_slug=None):
    active_school = require_active_school(request, school_slug)
    status_filter = request.GET.get('status', 'all')
    templates = (
        filter_for_school(ResultTemplate.objects, active_school)
        .filter(status=ResultTemplateStatus.OPEN)
        .prefetch_related('subjects')
        .order_by('-created_at')
    )
    template_rows = []
    for template in templates:
        all_subject_rows = build_subject_progress_rows(template)
        subject_rows = filter_subject_rows(all_subject_rows, status_filter)
        pending_subjects = sum(1 for row in all_subject_rows if row['status_label'] != 'Submitted')
        if status_filter != 'all' and not subject_rows:
            continue
        template_rows.append(
            {
                'template': template,
                'subject_rows': subject_rows,
                'pending_subjects': pending_subjects,
            }
        )
    return render(
        request,
        'attendance/result_teacher_hub.html',
        {
            'template_rows': template_rows,
            'status_filter': status_filter,
            'active_school': active_school,
        },
    )


@user_passes_test(is_teacher_or_mgmt)
def teacher_result_entry(request, template_id, subject_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(
        ResultTemplate.objects.prefetch_related('students', 'subjects'),
        pk=template_id,
        school=active_school,
    )
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'teacher_result_hub')

    subject = get_object_or_404(ResultTemplateSubject, pk=subject_id, template=template)
    students = list(
        template.students.only('id', 'candidate_no', 'row_number').order_by('row_number', 'candidate_no')
    )
    existing_entries = {
        entry.student_id: entry
        for entry in ResultEntry.objects.filter(template=template, subject=subject).only('student_id', 'raw_score', 'is_final')
    }
    can_edit = template.can_edit
    subject_progress = build_single_subject_progress(template, subject)

    if request.method == 'POST':
        form = ResultEntryBulkForm(request.POST)
        if not can_edit:
            messages.error(request, "This template is closed. You can no longer edit the submitted marks.")
            return redirect('teacher_result_entry', template_id=template.id, subject_id=subject.id)

        if form.is_valid():
            submit_mode = form.cleaned_data['submit_mode']
            now = timezone.now()
            for student in students:
                score = (request.POST.get(f'score_{student.id}', '') or '').strip().upper()
                defaults = {
                    'raw_score': score,
                    'submitted_by': request.user,
                    'is_final': submit_mode == 'final',
                }
                if submit_mode == 'final':
                    defaults['submitted_at'] = now

                ResultEntry.objects.update_or_create(
                    template=template,
                    subject=subject,
                    student=student,
                    defaults=defaults,
                )

            success_message = "Marks submitted successfully." if submit_mode == 'final' else "Draft saved successfully."
            messages.success(request, success_message)
            return redirect('teacher_result_entry', template_id=template.id, subject_id=subject.id)
        messages.error(request, "Please correct the invalid marks and try again.")
    else:
        form = ResultEntryBulkForm(initial={'submit_mode': 'draft'})

    rows = []
    for student in students:
        entry = existing_entries.get(student.id)
        rows.append(
            {
                'student': student,
                'score': entry.raw_score if entry else '',
                'is_final': entry.is_final if entry else False,
            }
        )

    context = {
        'template': template,
        'subject': subject,
        'rows': rows,
        'form': form,
        'can_edit': can_edit,
        'subject_progress': subject_progress,
        'analysis_available': subject_has_saved_marks(template, subject),
        'active_school': active_school,
    }
    return render(request, 'attendance/result_entry_form.html', context)


@user_passes_test(is_teacher_or_mgmt)
def autosave_result_entry(request, template_id, subject_id, school_slug=None):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)

    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate.objects.prefetch_related('students'), pk=template_id, school=active_school)
    subject = get_object_or_404(ResultTemplateSubject, pk=subject_id, template=template)
    if not template.can_edit:
        return JsonResponse({'ok': False, 'error': 'This template is closed.'}, status=400)

    scores = request.POST
    now = timezone.now()
    for student in template.students.all():
        field_name = f'score_{student.id}'
        if field_name not in scores:
            continue
        score = (scores.get(field_name, '') or '').strip().upper()
        ResultEntry.objects.update_or_create(
            template=template,
            subject=subject,
            student=student,
            defaults={
                'raw_score': score,
                'submitted_by': request.user,
                'is_final': False,
                'submitted_at': None,
            },
        )

    progress = build_single_subject_progress(template, subject)
    return JsonResponse(
        {
            'ok': True,
            'saved_at': now.strftime('%H:%M'),
            'entered_count': progress['entered_count'] if progress else 0,
            'final_count': progress['final_count'] if progress else 0,
            'pending_count': progress['pending_count'] if progress else 0,
            'status_label': progress['status_label'] if progress else 'Pending',
        }
    )

# --- DATA ENTRY VIEWS ---

@user_passes_test(is_teacher_or_mgmt)
def take_attendance(request, school_slug=None):
    """Form for teachers to submit class attendance."""
    active_school = require_active_school(request, school_slug)
    today = timezone.now().date()
    
    attendance_queryset = filter_for_school(AttendanceRecord.objects, active_school)
    submitted_today = list(attendance_queryset.filter(date=today).values_list('school_class', flat=True))
    existing_classes = attendance_queryset.values_list('school_class', flat=True).distinct().order_by('school_class')

    if request.method == "POST":
        form = AttendanceForm(request.POST)
        if form.is_valid():
            selected_class = form.cleaned_data['school_class']

            AttendanceRecord.objects.update_or_create(
                school=active_school,
                school_class=selected_class,
                date=today,
                defaults=form.cleaned_data 
            )

            messages.success(request, f"Attendance for {selected_class} recorded successfully!")
            return redirect('teacher_hub')
        else:
            messages.error(request, "Error in entry. Please check totals.")
    else:
        form = AttendanceForm()
    
    context = {
        'form': form, 
        'existing_classes': existing_classes,
        'submitted_today': submitted_today,
        'today': today,
        'active_school': active_school,
    }
    
    return render(request, 'attendance/entry_form.html', context)


@user_passes_test(is_teacher_or_mgmt)
def submit_tod_report(request, school_slug=None):
    """
    Handles the Daily TOD Report. 
    Uses form.save() to automatically capture arrival_time, departure_time, 
    maintenance_notes, and overall_comments.
    """
    active_school = require_active_school(request, school_slug)
    today = timezone.now().date()
    # Check if a report already exists for today to allow updating instead of duplicating
    existing_report = filter_for_school(DailyTODReport.objects, active_school).filter(date=today).first()

    if request.method == 'POST':
        # If existing_report is found, 'instance' tells Django to UPDATE that specific row
        form = TODReportForm(request.POST, instance=existing_report)
        
        if form.is_valid():
            report = form.save(commit=False)
            report.school = active_school
            report.date = today
            report.submitted_by = request.user
            
            # This save() call captures ALL fields from your modal:
            # arrival_time, departure_time, maintenance_notes, overall_comments, etc.
            report.save()
            
            messages.success(request, f"Daily TOD Report for {today} has been saved.")
            return redirect('teacher_hub') 
        else:
            messages.error(request, "There was an error in the form. Please check your times.")
    else:
        form = TODReportForm(instance=existing_report)

    context = {
        'form': form,
        'today': today,
        'is_update': existing_report is not None,
        'active_school': active_school,
    }
    return render(request, 'attendance/tod_report_form.html', context)

# --- MANAGEMENT DASHBOARD ---

@user_passes_test(is_management)
def master_dashboard(request, school_slug=None):
    """Main oversight dashboard for Headmaster / Management."""
    active_school = require_active_school(request, school_slug)
    date_str = request.GET.get('search_date') or request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    current_hour = datetime.now().hour
    
    # Check if it's 11:00 AM or later (Tanzania time is UTC+3)
    # If your server is on UTC, 11:00 AM EAT is 08:00 AM UTC.
    if current_hour >= 8: 
        logs_for_today = filter_for_school(NotificationLog.objects, active_school).filter(date=today).order_by('id')
        log = logs_for_today.first()
        if not log:
            log = NotificationLog.objects.create(date=today, school=active_school)
        elif logs_for_today.filter(sent=True).exists() and not log.sent:
            log.sent = True
            log.save(update_fields=['sent'])
        if not log.sent:
            reminder_result = send_staff_reminder("You are reminded to submit your attendance and/or T.O.D reports to M.D if you have not done so.")
            if reminder_result.get('ok'):
                log.sent = True
                log.save(update_fields=['sent'])
    
    latest_report = filter_for_school(DailyTODReport.objects, active_school).filter(date=today).first()
    recent_reports = filter_for_school(DailyTODReport.objects, active_school).order_by('-date')[:5]
    attendance_queryset = filter_for_school(AttendanceRecord.objects, active_school)
    records = attendance_queryset.filter(date=today).order_by('school_class')
    
    grand_total = records.aggregate(
        rb=Sum('total_boys_registered'), rg=Sum('total_girls_registered'),
        b=Sum('present_boys'), g=Sum('present_girls'),
        pb=Sum('permitted_boys'), pg=Sum('permitted_girls'),
        tb=Sum('truant_boys'), tg=Sum('truant_girls')
    )
    for key in grand_total:
        if grand_total[key] is None: grand_total[key] = 0

    total_reg = (grand_total['rb'] or 0) + (grand_total['rg'] or 0)
    total_pres = (grand_total['b'] or 0) + (grand_total['g'] or 0)
    school_attendance_pct = round((total_pres / total_reg * 100), 1) if total_reg > 0 else 0
    school_truancy_pct = round(((grand_total['tb'] + grand_total['tg']) / total_reg * 100), 1) if total_reg > 0 else 0

    yesterday = today - timedelta(days=1)
    y_total = attendance_queryset.filter(date=yesterday).aggregate(
        rb=Sum('total_boys_registered'), rg=Sum('total_girls_registered'),
        b=Sum('present_boys'), g=Sum('present_girls')
    )
    y_reg = (y_total['rb'] or 0) + (y_total['rg'] or 0)
    y_pres = (y_total['b'] or 0) + (y_total['g'] or 0)
    yesterday_pct = round((y_pres / y_reg * 100), 1) if y_reg > 0 else 0
    trend_diff = round(school_attendance_pct - yesterday_pct, 1)

    monthly_data = attendance_queryset.filter(date__year=today.year).annotate(
        month=TruncMonth('date'),
        calc_pct=ExpressionWrapper(
            (F('present_boys') + F('present_girls')) * 100.0 / (F('total_boys_registered') + F('total_girls_registered')),
            output_field=FloatField()
        )
    ).values('month').annotate(avg_attendance=Avg('calc_pct')).order_by('month')

    cache_key = f"ai_insight_{today}"
    ai_insight_text = cache.get(cache_key)

    if not ai_insight_text:
        ai_data_summary = [
            f"{r.school_class}: {round((r.present_boys + r.present_girls)/(r.total_boys_registered + r.total_girls_registered)*100, 1)}% attendance"
            for r in records if (r.total_boys_registered + r.total_girls_registered) > 0
        ]
        tod_notes = latest_report.maintenance_notes if latest_report else "No maintenance notes."
        ai_insight_text = get_ai_insights(ai_data_summary, tod_notes, active_school.display_name if active_school else "School Digital")
        cache.set(cache_key, ai_insight_text, 3600)

    active_staff = User.objects.filter(
        last_login__date=today
    ).filter(
        Q(groups__name__in=['Teachers', 'Management']) | Q(is_superuser=True)
    )
    if active_school:
        active_staff = active_staff.filter(school_memberships__school=active_school)
    active_staff = active_staff.distinct().order_by('-last_login')

    prev_day = today - timedelta(days=1)
    next_day = today + timedelta(days=1)
    result_templates = (
        filter_for_school(ResultTemplate.objects, active_school).annotate(
            subject_count=Count('subjects', distinct=True),
            submission_count=Count('entries', distinct=True),
        )
        .order_by('-created_at')[:5]
    )

    context = {
        'records': records,
        'grand_total': grand_total,
        'today': today,
        'school_attendance_pct': school_attendance_pct,
        'school_truancy_pct': school_truancy_pct,
        'trend_diff': trend_diff,
        'latest_report': latest_report,
        'recent_reports': recent_reports,
        'ai_insight_text': ai_insight_text, 
        'monthly_labels': [d['month'].strftime("%b") for d in monthly_data],
        'monthly_values': [round(d['avg_attendance'], 1) for d in monthly_data],
        'prev_day': prev_day,
        'next_day': next_day,
        'active_staff': active_staff,
        'result_templates': result_templates,
        'has_truants': any(r.truant_names and r.truant_names != "None" for r in records),
        'active_school': active_school,
    }
    return render(request, 'attendance/dashboard.html', context)


@user_passes_test(is_results_office)
def result_template_dashboard(request, school_slug=None):
    active_school = require_active_school(request, school_slug)
    if request.method == 'POST':
        form = ResultTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
            template.school = active_school
            template.uploaded_by = request.user
            template.status = ResultTemplateStatus.OPEN
            template.opened_at = timezone.now()
            template.save()
            try:
                template.rebuild_structure()
            except Exception as exc:
                template.processing_error = str(exc)
                template.save(update_fields=['processing_error', 'updated_at'])
                messages.error(request, f"Template uploaded, but parsing failed: {exc}")
            else:
                messages.success(request, "Template uploaded and prepared successfully.")
            return redirect('result_template_dashboard')
    else:
        form = ResultTemplateForm()

    templates = (
        filter_for_school(ResultTemplate.objects, active_school).annotate(
            subject_count=Count('subjects', distinct=True),
            student_count=Count('students', distinct=True),
            entry_count=Count('entries', distinct=True),
        )
        .order_by('-created_at')
    )
    template_rows = []
    for template in templates:
        subject_rows = build_subject_progress_rows(template)
        pending_subjects = sum(1 for row in subject_rows if row['status_label'] != 'Submitted')
        submitted_subjects = sum(1 for row in subject_rows if row['status_label'] == 'Submitted')
        template_rows.append(
            {
                'template': template,
                'pending_subjects': pending_subjects,
                'submitted_subjects': submitted_subjects,
            }
        )
    return render(
        request,
        'attendance/result_template_dashboard.html',
        {
            'form': form,
            'template_rows': template_rows,
            'pending_template_rows': [row for row in template_rows if row['pending_subjects']],
            'active_school': active_school,
        },
    )


@user_passes_test(is_results_office)
def result_template_detail(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(
        ResultTemplate.objects.prefetch_related('subjects', 'students'),
        pk=template_id,
        school=active_school,
    )
    status_filter = request.GET.get('status', 'all')
    all_subject_rows = build_subject_progress_rows(template)
    subject_rows = filter_subject_rows(all_subject_rows, status_filter)
    analysis_ready = (
        template.status == ResultTemplateStatus.CLOSED
        and any(row['status_label'] == 'Submitted' for row in all_subject_rows)
    )

    status_form = ResultTemplateStatusForm(initial={'status': template.status})
    return render(
        request,
        'attendance/result_template_detail.html',
        {
            'template': template,
            'subject_rows': subject_rows,
            'status_form': status_form,
            'status_filter': status_filter,
            'all_subject_rows': all_subject_rows,
            'analysis_ready': analysis_ready,
            'active_school': active_school,
        },
    )


@user_passes_test(is_results_office)
def update_result_template_status(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if request.method != 'POST':
        return redirect('result_template_detail', template_id=template.id)

    form = ResultTemplateStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Could not update template status.")
        return redirect('result_template_detail', template_id=template.id)

    template.status = form.cleaned_data['status']
    now = timezone.now()
    if template.status == ResultTemplateStatus.OPEN:
        template.opened_at = now
    elif template.status in {ResultTemplateStatus.CLOSED, ResultTemplateStatus.WITHDRAWN}:
        template.closed_at = now
    template.save(update_fields=['status', 'opened_at', 'closed_at', 'updated_at'])
    messages.success(request, f"Template status changed to {template.get_status_display()}.")
    return redirect('result_template_detail', template_id=template.id)


@user_passes_test(is_results_office)
def export_result_template_excel(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'result_template_detail', template_id=template.id)

    workbook_stream = build_export_workbook(template)
    return FileResponse(
        workbook_stream,
        as_attachment=True,
        filename=export_filename_for_template(template),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@user_passes_test(is_results_office)
def export_missing_submissions_report(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    subject_rows = [row for row in build_subject_progress_rows(template) if row['status_label'] != 'Submitted']

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="missing_submissions_{template.id}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Template', 'Subject', 'Status', 'Final Submitted', 'Total Students', 'Pending', 'Last Updated By', 'Last Updated At'])

    for row in subject_rows:
        writer.writerow(
            [
                template.name,
                row['subject'].name,
                row['status_label'],
                row['final_count'],
                row['total_students'],
                row['pending_count'],
                row['latest_updated_by'],
                row['latest_updated_at'].strftime('%Y-%m-%d %H:%M') if row['latest_updated_at'] else '',
            ]
        )

    return response


@user_passes_test(is_results_office)
def delete_result_template(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if request.method != 'POST':
        return redirect('result_template_detail', template_id=template.id)

    template_name = template.name
    workbook_path = template.workbook.path if template.workbook else None
    template.delete()

    if workbook_path and os.path.exists(workbook_path):
        try:
            os.remove(workbook_path)
        except OSError:
            pass

    messages.success(request, f'Template "{template_name}" was deleted successfully.')
    return redirect('result_template_dashboard')


@user_passes_test(is_results_office)
def result_template_analysis(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'result_template_detail', template_id=template.id)

    if template.status != ResultTemplateStatus.CLOSED:
        messages.error(request, "Analysis is only available after the template has been closed.")
        return redirect('result_template_detail', template_id=template.id)

    analysis_rows = build_subject_analysis_rows(template)
    if not analysis_rows:
        messages.error(request, "No fully submitted subjects are available for analysis yet.")
        return redirect('result_template_detail', template_id=template.id)

    return render(
        request,
        'attendance/result_template_analysis.html',
        {
            'template': template,
            'analysis_rows': analysis_rows,
        },
    )


@user_passes_test(is_results_office)
def export_result_template_analysis_pdf(request, template_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'result_template_detail', template_id=template.id)

    if template.status != ResultTemplateStatus.CLOSED:
        messages.error(request, "Analysis export is only available after the template has been closed.")
        return redirect('result_template_detail', template_id=template.id)

    analysis_rows = build_subject_analysis_rows(template)
    if not analysis_rows:
        messages.error(request, "No fully submitted subjects are available for analysis export yet.")
        return redirect('result_template_detail', template_id=template.id)

    return render_analysis_pdf_response(
        request,
        template,
        analysis_rows,
        title=f"{template.name} Subject Analysis",
        subtitle="Submitted subjects split by gender using the template grading rules.",
        filename=f"analysis_{template.id}.pdf",
    )


@user_passes_test(is_teacher_or_mgmt)
def teacher_subject_analysis(request, template_id, subject_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'teacher_result_hub')

    subject = get_object_or_404(ResultTemplateSubject, pk=subject_id, template=template)
    analysis_row = build_single_subject_analysis(template, subject, final_only=False)

    if not analysis_has_data(analysis_row):
        messages.error(request, "No saved marks are available for subject analysis yet.")
        return redirect('teacher_result_entry', template_id=template.id, subject_id=subject.id)

    return render(
        request,
        'attendance/result_subject_analysis.html',
        {
            'template': template,
            'subject': subject,
            'analysis_row': analysis_row,
            'active_school': active_school,
        },
    )


@user_passes_test(is_teacher_or_mgmt)
def export_teacher_subject_analysis_pdf(request, template_id, subject_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    template = get_object_or_404(ResultTemplate, pk=template_id, school=active_school)
    if not template_workbook_is_available(template):
        return redirect_missing_template_workbook(request, template, 'teacher_result_hub')

    subject = get_object_or_404(ResultTemplateSubject, pk=subject_id, template=template)
    analysis_row = build_single_subject_analysis(template, subject, final_only=False)

    if not analysis_has_data(analysis_row):
        messages.error(request, "No saved marks are available for subject analysis export yet.")
        return redirect('teacher_result_entry', template_id=template.id, subject_id=subject.id)

    return render_analysis_pdf_response(
        request,
        template,
        [analysis_row],
        title=f"{subject.name} Subject Analysis",
        subtitle="Current saved marks split by gender using the template grading rules.",
        filename=f"subject_analysis_{template.id}_{subject.id}.pdf",
    )

# --- EXPORTS (MANAGEMENT ONLY) ---


@user_passes_test(is_management)
def export_attendance_pdf(request, school_slug=None):
    active_school = require_active_school(request, school_slug)
    date_str = request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    records = filter_for_school(AttendanceRecord.objects, active_school).filter(date=today).order_by('school_class')
    
    # Use the same logo path logic you fixed earlier
    logo_path = resolve_school_logo_path(active_school)
    
    grand_total = records.aggregate(
        rb=Sum('total_boys_registered'), rg=Sum('total_girls_registered'),
        b=Sum('present_boys'), g=Sum('present_girls'),
        pb=Sum('permitted_boys'), pg=Sum('permitted_girls'),
        tb=Sum('truant_boys'), tg=Sum('truant_girls')
    )
    
    # Clean up None values
    for key in grand_total:
        if grand_total[key] is None: grand_total[key] = 0

    # --- CALCULATE PERCENTAGES ---
    total_registered = grand_total['rb'] + grand_total['rg']
    total_present = grand_total['b'] + grand_total['g']
    total_truant = grand_total['tb'] + grand_total['tg']

    if total_registered > 0:
        school_attendance_pct = (total_present / total_registered) * 100
        school_truancy_pct = (total_truant / total_registered) * 100
    else:
        school_attendance_pct = 0
        school_truancy_pct = 0

    context = {
        'records': records, 
        'grand_total': grand_total, 
        'today': today, 
        'school_name': active_school.display_name if active_school else 'School Digital',
        'logo_path': logo_path,
        'school_attendance_pct': round(school_attendance_pct, 1), # Rounding for a clean PDF look
        'school_truancy_pct': round(school_truancy_pct, 1),
        'user': request.user, # Pass the user to the template for footer info
    }

    template = get_template('attendance/pdf_template.html')
    html = template.render(context)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Attendance_Report_{today}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_tod_pdf(request, report_id, school_slug=None):
    active_school = require_active_school(request, school_slug)
    report = get_object_or_404(DailyTODReport, id=report_id, school=active_school)
    logo_path = resolve_school_logo_path(active_school)
    context = {'report': report, 'school_name': active_school.display_name if active_school else 'School Digital', 'logo_path': logo_path, 'user': request.user}
    html = get_template('attendance/pdf_template2.html').render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="TOD_Report_{report.date}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_weekly_tod_summary(request, school_slug=None):
    active_school = require_active_school(request, school_slug)
    date_str = request.GET.get('date')
    active_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    start = active_date - timedelta(days=active_date.weekday())
    end = start + timedelta(days=6)
    reports = filter_for_school(DailyTODReport.objects, active_school).filter(date__range=[start, end]).order_by('date')
    logo_path = resolve_school_logo_path(active_school)
    
    context = {'reports': reports, 'start_date': start, 'end_date': end, 'school_name': active_school.display_name.upper() if active_school else 'SCHOOL DIGITAL', 'logo_path': logo_path, 'user': request.user}
    html = get_template('attendance/weekly_summary_pdf.html').render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Weekly_TOD_Summary_{start}_to_{end}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_weekly_truants_pdf(request, school_slug=None):
    active_school = require_active_school(request, school_slug)
    today = timezone.now().date()
    start_date = today - timedelta(days=7)
    
    records = filter_for_school(AttendanceRecord.objects, active_school).filter(
        date__range=[start_date, today]
    ).order_by('school_class')

    stats = {}
    for r in records:
        if r.truant_names and r.truant_names.lower() != "none":
            names_list = [name.strip() for name in r.truant_names.split(',')]
            for name in names_list:
                if name:
                    key = (name, r.school_class)
                    stats[key] = stats.get(key, 0) + 1

    report_data = []
    for (name, s_class), count in stats.items():
        report_data.append({
            'name': name,
            'class': s_class.upper(),
            'total_missed': count
        })

    report_data = sorted(report_data, key=lambda x: (x['class'], x['name']))
    logo_path = resolve_school_logo_path(active_school)

    context = {
        'report_data': report_data,
        'start_date': start_date,
        'end_date': today,
        'title': "WEEKLY TRUANCY SUMMARY",
        'school_name': active_school.display_name.upper() if active_school else 'SCHOOL DIGITAL',
        'logo_path': logo_path,
        'user': request.user
    }

    from django.template.loader import render_to_string
    html_string = render_to_string('attendance/weekly_pdf_template.html', context)

    response = HttpResponse(content_type='application/pdf')
    filename = f"Weekly_Report_{timezone.now().date()}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    pisa_status = pisa.CreatePDF(html_string, dest=response)

    if pisa_status.err:
        return HttpResponse('We had some errors <pre>' + html_string + '</pre>')
    
    return response
