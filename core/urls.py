"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from attendance import views
from attendance.views import export_attendance_pdf, master_dashboard, take_attendance # Good, you have this
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', views.school_directory, name='school_directory'),
    path('home/', views.home_redirect, name='home'),
    path('platform/login/', views.platform_admin_login, name='platform_admin_login'),
    path('platform/schools/', views.platform_school_dashboard, name='platform_school_dashboard'),
    path('platform/schools/<int:school_id>/edit/', views.edit_school, name='edit_school'),
    path('platform/schools/<int:school_id>/switch/', views.switch_active_school, name='switch_active_school'),
    path('platform/accounts/<int:membership_id>/edit/', views.edit_school_account, name='edit_school_account'),
    path('platform/schools/<int:school_id>/delete/', views.delete_school, name='delete_school'),
    path('platform/schools/<int:school_id>/toggle-active/', views.toggle_school_active_status, name='toggle_school_active_status'),
    path('platform/accounts/<int:membership_id>/remove/', views.remove_school_account, name='remove_school_account'),
    path('manifest.webmanifest', views.web_manifest, name='web_manifest'),
    path('offline/', views.offline_page, name='offline_page'),
    path('service-worker.js', views.service_worker, name='service_worker'),
    path('ndi-admin/', admin.site.urls),
    path('admin/', RedirectView.as_view(url='/ndi-admin/', permanent=False)),
    path('entry/', take_attendance, name='take_attendance'),
    path('dashboard/', master_dashboard, name='master_dashboard'),
    path('export-pdf/', export_attendance_pdf, name='export_attendance_pdf'),
    path('accounts/login/', RedirectView.as_view(url='/', permanent=False)),
    path('accounts/logout/', RedirectView.as_view(url='/logout/', permanent=False)),
    path('accounts/', include('django.contrib.auth.urls')),
    # attendance/urls.py
    path('tod-report/', views.submit_tod_report, name='submit_tod_report'),
    path('export-pdf/<int:report_id>/', views.export_tod_pdf, name='export_tod_pdf'),
    path('export-weekly-pdf/', views.export_weekly_tod_summary, name='export_weekly_tod_summary'),
    path('login/', RedirectView.as_view(url='/', permanent=False), name='login'),
    path('login-check/', views.login_success_redirect, name='login_redirect'),
    path('schools/<slug:school_slug>/login/', views.school_login, name='school_login'),
    path('hub/', views.teacher_hub, name='teacher_hub'),
    path('hub/results/', views.teacher_result_hub, name='teacher_result_hub'),
    path('hub/results/<int:template_id>/<int:subject_id>/', views.teacher_result_entry, name='teacher_result_entry'),
    path('hub/results/<int:template_id>/<int:subject_id>/autosave/', views.autosave_result_entry, name='autosave_result_entry'),
    path('hub/results/<int:template_id>/<int:subject_id>/analysis/', views.teacher_subject_analysis, name='teacher_subject_analysis'),
    path('hub/results/<int:template_id>/<int:subject_id>/analysis/export/', views.export_teacher_subject_analysis_pdf, name='export_teacher_subject_analysis_pdf'),
    path('logout/', views.logout_user, name='logout'),
    path('export-weekly-truants-pdf/', views.export_weekly_truants_pdf, name='export_weekly_truants_pdf'),
    path('results/dashboard/', views.result_template_dashboard, name='result_template_dashboard'),
    path('results/<int:template_id>/', views.result_template_detail, name='result_template_detail'),
    path('results/<int:template_id>/status/', views.update_result_template_status, name='update_result_template_status'),
    path('results/<int:template_id>/export/', views.export_result_template_excel, name='export_result_template_excel'),
    path('results/<int:template_id>/analysis/', views.result_template_analysis, name='result_template_analysis'),
    path('results/<int:template_id>/analysis/export/', views.export_result_template_analysis_pdf, name='export_result_template_analysis_pdf'),

    # --- School-Specific Prefixed URLs (Multi-Tenant Support) ---
    path('schools/<slug:school_slug>/', include([
        path('hub/', views.teacher_hub, name='school_teacher_hub'),
        path('hub/results/', views.teacher_result_hub, name='school_teacher_result_hub'),
        path('hub/results/<int:template_id>/<int:subject_id>/', views.teacher_result_entry, name='school_teacher_result_entry'),
        path('hub/results/<int:template_id>/<int:subject_id>/autosave/', views.autosave_result_entry, name='school_autosave_result_entry'),
        path('hub/results/<int:template_id>/<int:subject_id>/analysis/', views.teacher_subject_analysis, name='school_teacher_subject_analysis'),
        path('hub/results/<int:template_id>/<int:subject_id>/analysis/export/', views.export_teacher_subject_analysis_pdf, name='school_export_teacher_subject_analysis_pdf'),
        
        path('entry/', take_attendance, name='school_take_attendance'),
        path('dashboard/', master_dashboard, name='school_master_dashboard'),
        path('export-pdf/', export_attendance_pdf, name='school_export_attendance_pdf'),
        
        path('tod-report/', views.submit_tod_report, name='school_submit_tod_report'),
        path('export-pdf/<int:report_id>/', views.export_tod_pdf, name='school_export_tod_pdf'),
        path('export-weekly-pdf/', views.export_weekly_tod_summary, name='school_export_weekly_tod_summary'),
        path('export-weekly-truants-pdf/', views.export_weekly_truants_pdf, name='school_export_weekly_truants_pdf'),
        
        path('results/dashboard/', views.result_template_dashboard, name='school_result_template_dashboard'),
        path('results/<int:template_id>/', views.result_template_detail, name='school_result_template_detail'),
        path('results/<int:template_id>/status/', views.update_result_template_status, name='school_update_result_template_status'),
        path('results/<int:template_id>/export/', views.export_result_template_excel, name='school_export_result_template_excel'),
        path('results/<int:template_id>/analysis/', views.result_template_analysis, name='school_result_template_analysis'),
        path('results/<int:template_id>/analysis/export/', views.export_result_template_analysis_pdf, name='school_export_result_template_analysis_pdf'),
    ])),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)