import os
import re
from datetime import timedelta
from io import BytesIO
import ssl
import httpx

# --- AI & Environment ---
from google import genai 
from dotenv import load_dotenv

# --- Django Core ---
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum, Avg, F, ExpressionWrapper, FloatField, Q
from django.utils import timezone
from django.http import HttpResponse
from django.template.loader import get_template
from django.contrib.auth.decorators import user_passes_test, login_required
from django.db.models.functions import Upper, TruncMonth
from django.core.cache import cache
from django.contrib.auth.models import User
from django.db import models

# --- PDF Generation ---
from xhtml2pdf import pisa

# --- Project Models & Forms ---
from .models import AttendanceRecord, DailyTODReport
from .forms import AttendanceForm, TODReportForm

load_dotenv()

# --- PERMISSION HELPERS ---

def is_management(user):
    """Checks if the user belongs to the Management group or is a Superuser."""
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name='Management').exists())

def is_teacher_or_mgmt(user):
    """Checks if the user is a Teacher, Management, or Superuser."""
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=['Teachers', 'Management']).exists())

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

def login_success_redirect(request):
    """Debug version of the Traffic Cop to catch group name errors."""
    if not request.user.is_authenticated:
        return HttpResponse("DEBUG ERROR: You are not even logged in.")

    # Get a list of all groups this user actually has
    user_groups = list(request.user.groups.values_list('name', flat=True))
    
    if request.user.is_superuser or 'Management' in user_groups:
        return redirect('master_dashboard')
    
    elif 'Teachers' in user_groups:
        return redirect('teacher_hub')
    
    else:
        # If they reach here, they are logged in but in the WRONG group
        return HttpResponse(f"""
            <h1>Group Mismatch Error</h1>
            <p><strong>User:</strong> {request.user.username}</p>
            <p><strong>Groups found in database:</strong> {user_groups}</p>
            <p><strong>Required:</strong> 'Teachers' or 'Management' (Case-Sensitive!)</p>
            <a href="/admin/">Go to Admin to fix Groups</a>
        """)

@login_required
def teacher_hub(request):
    """Landing page for teachers to choose between Attendance or TOD tasks."""
    return render(request, 'attendance/teacher_hub.html')

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
    """Form for Teacher on Duty (TOD) to file daily compound reports."""
    today = timezone.now().date()
    tod_report_exists = DailyTODReport.objects.filter(date=today).exists()

    if request.method == 'POST':
        form = TODReportForm(request.POST)
        if form.is_valid():
            teacher_name = form.cleaned_data['teacher_name']
            
            DailyTODReport.objects.update_or_create(
                date=today,
                defaults={
                    'submitted_by': request.user,
                    'teacher_name': teacher_name,
                    'maintenance_notes': form.cleaned_data.get('maintenance_notes', ''),
                    'general_observations': form.cleaned_data.get('general_observations', ''),
                }
            )
            
            messages.success(request, f"SUCCESS: The TOD Daily Report for {today} has been filed/updated.")
            return redirect('teacher_hub') 
    else:
        existing_report = DailyTODReport.objects.filter(date=today).first()
        if existing_report:
            form = TODReportForm(instance=existing_report)
        else:
            form = TODReportForm()

    context = {
        'form': form,
        'tod_report_exists': tod_report_exists,
        'today': today
    }
    
    return render(request, 'attendance/tod_report_form.html', context)

# --- MANAGEMENT DASHBOARD ---

@user_passes_test(is_management)
def master_dashboard(request):
    """Main oversight dashboard for Headmaster / Management."""
    date_str = request.GET.get('search_date') or request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    
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
    }
    return render(request, 'attendance/dashboard.html', context)

# --- EXPORTS (MANAGEMENT ONLY) ---

@user_passes_test(is_management)
def export_attendance_pdf(request):
    date_str = request.GET.get('date')
    today = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
    records = AttendanceRecord.objects.filter(date=today).order_by('school_class')
    
    grand_total = records.aggregate(
        rb=Sum('total_boys_registered'), rg=Sum('total_girls_registered'),
        b=Sum('present_boys'), g=Sum('present_girls'),
        pb=Sum('permitted_boys'), pg=Sum('permitted_girls'),
        tb=Sum('truant_boys'), tg=Sum('truant_girls')
    )
    for key in grand_total:
        if grand_total[key] is None: grand_total[key] = 0

    context = {'records': records, 'grand_total': grand_total, 'today': today, 'school_name': 'Naura Secondary School'}
    template = get_template('attendance/pdf_template.html')
    html = template.render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Attendance_Report_{today}.pdf"'
    
    pisa.CreatePDF(html, dest=response)
    return response

@user_passes_test(is_management)
def export_tod_pdf(request, report_id):
    report = DailyTODReport.objects.get(id=report_id)
    context = {'report': report, 'school_name': 'Naura Secondary School'}
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
    
    context = {'reports': reports, 'start_date': start, 'end_date': end, 'school_name': 'NAURA SECONDARY'}
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

    context = {
        'report_data': report_data,
        'start_date': start_date,
        'end_date': today,
        'title': "WEEKLY TRUANCY SUMMARY"
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