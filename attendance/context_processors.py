from .models import School


def active_school_context(request):
    active_school = None
    if request.user.is_authenticated:
        if request.user.is_superuser:
            school_id = request.session.get("active_school_id")
            if school_id:
                active_school = School.objects.filter(id=school_id, is_active=True).first()
            if not active_school:
                active_school = School.objects.filter(is_active=True).order_by("name").first()
        else:
            active_school = (
                School.objects.filter(is_active=True, memberships__user=request.user)
                .distinct()
                .order_by("name")
                .first()
            )

    return {
        "active_school": active_school,
        "platform_name": active_school.initiative_name if active_school else "Naura Digital Initiative",
        "platform_short_name": active_school.initiative_short_name if active_school else "N.D.I",
        "school_display_name": active_school.display_name if active_school else "School Digital",
        "platform_brand_name": "Naura Digital Initiative",
        "platform_brand_short_name": "N.D.I",
        "platform_brand_logo": "assets/img/nauraicon.jpg",
        "platform_brand_logo_no_bg": "assets/img/nauraicon.svg",
        "platform_brand_banner": "assets/img/naura-banner.jpg",
    }


def portal_roles(request):
    user = request.user
    if not user.is_authenticated:
        return {
            "can_access_teacher_hub": False,
            "can_access_academic_office": False,
            "can_access_management": False,
            "can_access_platform_admin": False,
        }

    group_names = set(user.groups.values_list("name", flat=True))
    is_superuser = user.is_superuser

    return {
        "can_access_teacher_hub": is_superuser or bool(group_names.intersection({"Teachers", "Management", "Academic Office"})),
        "can_access_academic_office": is_superuser or bool(group_names.intersection({"Management", "Academic Office"})),
        "can_access_management": is_superuser or "Management" in group_names,
        "can_access_platform_admin": is_superuser,
    }
