def portal_roles(request):
    user = request.user
    if not user.is_authenticated:
        return {
            "can_access_teacher_hub": False,
            "can_access_academic_office": False,
            "can_access_management": False,
        }

    group_names = set(user.groups.values_list("name", flat=True))
    is_superuser = user.is_superuser

    return {
        "can_access_teacher_hub": is_superuser or bool(group_names.intersection({"Teachers", "Management", "Academic Office"})),
        "can_access_academic_office": is_superuser or bool(group_names.intersection({"Management", "Academic Office"})),
        "can_access_management": is_superuser or "Management" in group_names,
    }
