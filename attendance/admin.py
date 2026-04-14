from django.contrib import admin, messages

from .models import (
    AttendanceRecord,
    ResultEntry,
    ResultStudent,
    ResultTemplate,
    ResultTemplateSubject,
)


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
    fieldsets = (
        ('Class Info', {'fields': ('school_class', 'date')}),
        ('Presentees', {'fields': ('present_boys', 'present_girls')}),
        ('With Permission', {'fields': ('permitted_boys', 'permitted_girls', 'permitted_names')}),
        ('Truants', {'fields': ('truant_boys', 'truant_girls', 'truant_names')}),
    )


class ResultTemplateSubjectInline(admin.TabularInline):
    model = ResultTemplateSubject
    extra = 0
    readonly_fields = ('name', 'column_letter', 'column_index', 'display_order')


class ResultStudentInline(admin.TabularInline):
    model = ResultStudent
    extra = 0
    readonly_fields = ('row_number', 'candidate_no', 'student_name', 'sex')
    can_delete = False
    show_change_link = False


@admin.register(ResultTemplate)
class ResultTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'sheet_name', 'last_processed_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'sheet_name')
    inlines = [ResultTemplateSubjectInline, ResultStudentInline]
    readonly_fields = (
        'sheet_name',
        'header_row',
        'first_student_row',
        'last_student_row',
        'last_processed_at',
        'processing_error',
        'opened_at',
        'closed_at',
    )

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by_id:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
        if 'workbook' in form.changed_data or not change:
            try:
                obj.rebuild_structure()
            except Exception as exc:
                obj.processing_error = str(exc)
                obj.save(update_fields=['processing_error', 'updated_at'])
                self.message_user(
                    request,
                    f"Template saved, but parsing failed: {exc}",
                    level=messages.ERROR,
                )
            else:
                self.message_user(request, "Template workbook parsed successfully.", level=messages.SUCCESS)


@admin.register(ResultTemplateSubject)
class ResultTemplateSubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'template', 'column_letter', 'display_order')
    list_filter = ('template',)
    search_fields = ('name', 'template__name')


@admin.register(ResultStudent)
class ResultStudentAdmin(admin.ModelAdmin):
    list_display = ('candidate_no', 'student_name', 'sex', 'template', 'row_number')
    list_filter = ('template', 'sex')
    search_fields = ('candidate_no', 'student_name', 'template__name')


@admin.register(ResultEntry)
class ResultEntryAdmin(admin.ModelAdmin):
    list_display = ('student', 'subject', 'template', 'raw_score', 'is_final', 'updated_at')
    list_filter = ('template', 'subject', 'is_final')
    search_fields = ('student__student_name', 'student__candidate_no', 'subject__name', 'template__name')
