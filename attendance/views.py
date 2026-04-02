import os
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
from django.db.models.functions import Upper, TruncMonth
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db import models
from django.conf import settings

# --- PDF Generation ---
from xhtml2pdf import pisa

# --- Project Models & Forms ---
from .excel_utils import build_export_workbook, export_filename_for_template
from .forms import (
    AttendanceForm,
    ResultEntryBulkForm,
    ResultTemplateForm,
    ResultTemplateStatusForm,
    TODReportForm,
)
from .models import (
    AttendanceRecord,
    DailyTODReport,
    ResultEntry,
    ResultTemplate,
    ResultTemplateStatus,
    ResultTemplateSubject,
)

load_dotenv()

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


def build_subject_progress_rows(template):
    total_students = template.students.count()
    entry_queryset = ResultEntry.objects.filter(template=template).select_related('submitted_by', 'subject')
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

# --- HELPER: AI INSIGHTS ---

def get_ai_insights(monthly_data, tod_notes):
    """Fetches and cleans insights for a professional dashboard UI."""
    try:
        # THE FIX: Initialize the Gemini client properly
        # We handle the SSL/Timeout issues by passing a custom config if needed, 
        # but don't overwrite the 'client' variable with httpx.
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        
        prompt = f"""
        Analyze school data for Naura Secondary:
        Attendance: {monthly_data}
        TOD Notes: {tod_notes}
        
        STRICT FORMATTING:
        - Use "Summary:" as a heading.
        - Use a bulleted list for trends and recommendations.
        - Under 60 words. No conversational filler.
        """
        
        # Use gemini-2.0-flash (most stable for NDI dashboard speed)
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
    return redirect('login')

def login_success_redirect(request):
    """Debug version of the Traffic Cop to catch group name errors."""
    if not request.user.is_authenticated:
        return HttpResponse("DEBUG ERROR: You are not even logged in.")

    # Get a list of all groups this user actually has
    user_groups = list(request.user.groups.values_list('name', flat=True))
    
    if request.user.is_superuser or 'Management' in user_groups:
        return redirect('master_dashboard')

    elif 'Academic Office' in user_groups:
        return redirect('result_template_dashboard')
    
    elif 'Teachers' in user_groups:
        return redirect('teacher_hub')
    
    else:
        # If they reach here, they are logged in but in the WRONG group
        return HttpResponse(f"""
            <h1>Group Mismatch Error</h1>
            <p><strong>User:</strong> {request.user.username}</p>
            <p><strong>Groups found in database:</strong> {user_groups}</p>
            <p><strong>Required:</strong> 'Teachers', 'Academic Office', or 'Management' (Case-Sensitive!)</p>
            <a href="/admin/">Go to Admin to fix Groups</a>
        """)

@login_required
def teacher_hub(request):
    """Landing page for teachers to choose between Attendance or TOD tasks."""
    open_templates = (
        ResultTemplate.objects.filter(status=ResultTemplateStatus.OPEN)
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
        },
    )


@user_passes_test(is_teacher_or_mgmt)
def teacher_result_hub(request):
    status_filter = request.GET.get('status', 'all')
    templates = (
        ResultTemplate.objects.filter(status=ResultTemplateStatus.OPEN)
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
        },
    )


@user_passes_test(is_teacher_or_mgmt)
def teacher_result_entry(request, template_id, subject_id):
    template = get_object_or_404(
        ResultTemplate.objects.prefetch_related('students', 'subjects'),
        pk=template_id,
    )
    subject = get_object_or_404(ResultTemplateSubject, pk=subject_id, template=template)
    students = list(template.students.all())
    existing_entries = {
        entry.student_id: entry
        for entry in ResultEntry.objects.filter(template=template, subject=subject).select_related('student')
    }
    can_edit = template.can_edit
    subject_progress = next(
        (row for row in build_subject_progress_rows(template) if row['subject'].id == subject.id),
        None,
    )

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
    }
    return render(request, 'attendance/result_entry_form.html', context)


@user_passes_test(is_teacher_or_mgmt)
def autosave_result_entry(request, template_id, subject_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)

    template = get_object_or_404(ResultTemplate.objects.prefetch_related('students'), pk=template_id)
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

    progress = next(
        (row for row in build_subject_progress_rows(template) if row['subject'].id == subject.id),
        None,
    )
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
def take_attendance(request):
    """Form for teachers to submit class attendance."""
    today = timezone.now().date()
    
    submitted_today = list(AttendanceRecord.objects.filter(date=today).values_list('school_class', flat=True))
    existing_classes = AttendanceRecord.objects.values_list('school_class', flat=True).distinct().order_by('school_class')

    if request.method == "POST":
        form = AttendanceForm(request.POST)
        if form.is_valid():
            selected_class = form.cleaned_data['school_class']

            AttendanceRecord.objects.update_or_create(
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
        'today': today
    }
    
    return render(request, 'attendance/entry_form.html', context)


@user_passes_test(is_teacher_or_mgmt)
def submit_tod_report(request):
    """
    Handles the Daily TOD Report. 
    Uses form.save() to automatically capture arrival_time, departure_time, 
    maintenance_notes, and overall_comments.
    """
    today = timezone.now().date()
    # Check if a report already exists for today to allow updating instead of duplicating
    existing_report = DailyTODReport.objects.filter(date=today).first()

    if request.method == 'POST':
        # If existing_report is found, 'instance' tells Django to UPDATE that specific row
        form = TODReportForm(request.POST, instance=existing_report)
        
        if form.is_valid():
            report = form.save(commit=False)
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
        'is_update': existing_report is not None
    }
    return render(request, 'attendance/tod_report_form.html', context)

# --- MANAGEMENT DASHBOARD ---

@user_passes_test(is_management)
def master_dashboard(request):
    """Main oversight dashboard for Headmaster / Management."""
    date_str = request.GET.get('search_date') or request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    current_hour = datetime.now().hour
    
    # Check if it's 11:00 AM or later (Tanzania time is UTC+3)
    # If your server is on UTC, 11:00 AM EAT is 08:00 AM UTC.
    if current_hour >= 8: 
        log, created = NotificationLog.objects.get_or_create(date=today)
        if not log.sent:
            send_staff_reminder("You are reminded to submit your attendence and/or T.O.D reports to N.D.I if you have not done so.")
            log.sent = True
            log.save()
    
    latest_report = DailyTODReport.objects.filter(date=today).first()
    recent_reports = DailyTODReport.objects.order_by('-date')[:5]
    records = AttendanceRecord.objects.filter(date=today).order_by('school_class')
    
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
    y_total = AttendanceRecord.objects.filter(date=yesterday).aggregate(
        rb=Sum('total_boys_registered'), rg=Sum('total_girls_registered'),
        b=Sum('present_boys'), g=Sum('present_girls')
    )
    y_reg = (y_total['rb'] or 0) + (y_total['rg'] or 0)
    y_pres = (y_total['b'] or 0) + (y_total['g'] or 0)
    yesterday_pct = round((y_pres / y_reg * 100), 1) if y_reg > 0 else 0
    trend_diff = round(school_attendance_pct - yesterday_pct, 1)

    monthly_data = AttendanceRecord.objects.filter(date__year=today.year).annotate(
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
        ai_insight_text = get_ai_insights(ai_data_summary, tod_notes)
        cache.set(cache_key, ai_insight_text, 3600)

    active_staff = User.objects.filter(
        last_login__date=today
    ).filter(
        Q(groups__name__in=['Teachers', 'Management']) | Q(is_superuser=True)
    ).distinct().order_by('-last_login')

    prev_day = today - timedelta(days=1)
    next_day = today + timedelta(days=1)
    result_templates = (
        ResultTemplate.objects.annotate(
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
    }
    return render(request, 'attendance/dashboard.html', context)


@user_passes_test(is_results_office)
def result_template_dashboard(request):
    if request.method == 'POST':
        form = ResultTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
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
        ResultTemplate.objects.annotate(
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
        },
    )


@user_passes_test(is_results_office)
def result_template_detail(request, template_id):
    template = get_object_or_404(
        ResultTemplate.objects.prefetch_related('subjects', 'students'),
        pk=template_id,
    )
    status_filter = request.GET.get('status', 'all')
    all_subject_rows = build_subject_progress_rows(template)
    subject_rows = filter_subject_rows(all_subject_rows, status_filter)

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
        },
    )


@user_passes_test(is_results_office)
def update_result_template_status(request, template_id):
    template = get_object_or_404(ResultTemplate, pk=template_id)
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
def export_result_template_excel(request, template_id):
    template = get_object_or_404(ResultTemplate, pk=template_id)
    workbook_stream = build_export_workbook(template)
    return FileResponse(
        workbook_stream,
        as_attachment=True,
        filename=export_filename_for_template(template),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@user_passes_test(is_results_office)
def export_missing_submissions_report(request, template_id):
    template = get_object_or_404(ResultTemplate, pk=template_id)
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

# --- EXPORTS (MANAGEMENT ONLY) ---


@user_passes_test(is_management)
def export_attendance_pdf(request):
    date_str = request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    records = AttendanceRecord.objects.filter(date=today).order_by('school_class')
    
    # Use the same logo path logic you fixed earlier
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'img', 'nauraicon2.jpg')
    
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
        'school_name': 'Naura Secondary School', 
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
def export_tod_pdf(request, report_id):
    report = DailyTODReport.objects.get(id=report_id)
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'img', 'nauraicon2.jpg')
    context = {'report': report, 'school_name': 'Naura Secondary School', 'logo_path': logo_path, 'user': request.user}
    html = get_template('attendance/pdf_template2.html').render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="TOD_Report_{report.date}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_weekly_tod_summary(request):
    date_str = request.GET.get('date')
    active_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    start = active_date - timedelta(days=active_date.weekday())
    end = start + timedelta(days=6)
    reports = DailyTODReport.objects.filter(date__range=[start, end]).order_by('date')
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'img', 'nauraicon2.jpg')
    
    context = {'reports': reports, 'start_date': start, 'end_date': end, 'school_name': 'NAURA SECONDARY', 'logo_path': logo_path, 'user': request.user}
    html = get_template('attendance/weekly_summary_pdf.html').render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Weekly_TOD_Summary_{start}_to_{end}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_weekly_truants_pdf(request):
    today = timezone.now().date()
    start_date = today - timedelta(days=7)
    
    records = AttendanceRecord.objects.filter(
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
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'img', 'nauraicon2.jpg')

    context = {
        'report_data': report_data,
        'start_date': start_date,
        'end_date': today,
        'title': "WEEKLY TRUANCY SUMMARY",
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
