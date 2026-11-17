def nav_context(request):
    """
    Cung cấp danh mục chuyên mục diễn đàn cho thanh điều hướng Navbar (dropdown).
    """
    try:
        from forum.models import Category
        categories = list(Category.objects.all().order_by("name"))
    except Exception:
        categories = []
    return {
        "nav_categories": categories,
    }
