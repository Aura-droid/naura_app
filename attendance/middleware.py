from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .views import build_school_url_name, get_active_school, school_reverse


REDIRECTABLE_VIEWS = {
    'teacher_hub',
    'teacher_result_hub',
    'teacher_result_entry',
    'teacher_subject_analysis',
    'export_teacher_subject_analysis_pdf',
    'take_attendance',
    'submit_tod_report',
    'master_dashboard',
    'export_attendance_pdf',
    'export_tod_pdf',
    'export_weekly_tod_summary',
    'export_weekly_truants_pdf',
    'result_template_dashboard',
    'result_template_detail',
    'update_result_template_status',
    'export_result_template_excel',
    'result_template_analysis',
    'export_result_template_analysis_pdf',
    'export_missing_submissions_report',
    'delete_result_template',
}


class SchoolRoutingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and request.method == 'GET'
            and not request.path.startswith('/schools/')
        ):
            try:
                resolver_match = resolve(request.path_info)
            except Resolver404:
                resolver_match = None
            if resolver_match and resolver_match.view_name in REDIRECTABLE_VIEWS:
                school = get_active_school(request)
                if school:
                    target = school_reverse(resolver_match.view_name, school, **resolver_match.kwargs)
                    if target and target != request.path:
                        if request.META.get('QUERY_STRING'):
                            target = f"{target}?{request.META['QUERY_STRING']}"
                        return redirect(target)

        return self.get_response(request)
