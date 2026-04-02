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
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('entry/', take_attendance, name='take_attendance'),
    path('dashboard/', master_dashboard, name='master_dashboard'),
    path('export-pdf/', export_attendance_pdf, name='export_attendance_pdf'),
    path('accounts/', include('django.contrib.auth.urls')),
    # attendance/urls.py
    path('tod-report/', views.submit_tod_report, name='submit_tod_report'),
    path('export-pdf/<int:report_id>/', views.export_tod_pdf, name='export_tod_pdf'),
    path('export-weekly-pdf/', views.export_weekly_tod_summary, name='export_weekly_tod_summary'),
    path('login/', auth_views.LoginView.as_view(template_name='attendance/login.html'), name='login'),
    path('login-check/', views.login_success_redirect, name='login_redirect'),
    path('hub/', views.teacher_hub, name='teacher_hub'),
    path('hub/results/', views.teacher_result_hub, name='teacher_result_hub'),
    path('hub/results/<int:template_id>/<int:subject_id>/', views.teacher_result_entry, name='teacher_result_entry'),
    path('hub/results/<int:template_id>/<int:subject_id>/autosave/', views.autosave_result_entry, name='autosave_result_entry'),
    path('logout/', auth_views.LogoutView.as_view(http_method_names=['get', 'post', 'options'], next_page='login'), name='logout'),
    path('export-weekly-truants-pdf/', views.export_weekly_truants_pdf, name='export_weekly_truants_pdf'),
    path('results/dashboard/', views.result_template_dashboard, name='result_template_dashboard'),
    path('results/<int:template_id>/', views.result_template_detail, name='result_template_detail'),
    path('results/<int:template_id>/status/', views.update_result_template_status, name='update_result_template_status'),
    path('results/<int:template_id>/export/', views.export_result_template_excel, name='export_result_template_excel'),
    path('results/<int:template_id>/missing-submissions/', views.export_missing_submissions_report, name='export_missing_submissions_report'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
