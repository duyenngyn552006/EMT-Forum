"""Thanh dieu huong Trung tam kiem duyet (M6) - dung chung cho moi trang quan tri cua
module moderation va trang Dashboard."""
from django import template

from moderation.services import hidden_count, pending_counts

register = template.Library()

# url_name (theo namespace) -> muc dang mo tren thanh dieu huong
_ACTIVE_BY_URL = {
    "dashboard": {"overview": "dashboard"},
    "accounts": {"user_list": "users", "assign_role": "users", "toggle_active": "users"},
    "forum": {
        "category_list": "categories", "category_create": "categories",
        "category_edit": "categories", "category_delete": "categories",
    },
    "moderation": {
        "report_list": "reports", "report_resolve": "reports",
        "pending_content_list": "content", "approve_content": "content", "reject_content": "content",
        "hidden_content_list": "hidden", "restore_content": "hidden",
        "appeal_list": "appeals", "appeal_review": "appeals",
        "keyword_list": "keywords", "keyword_create": "keywords", "keyword_edit": "keywords", "keyword_delete": "keywords",
        "log_list": "logs",
    },
}


@register.inclusion_tag("moderation/_sidebar.html", takes_context=True)
def moderation_sidebar(context):
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    active = ""
    if match is not None:
        active = _ACTIVE_BY_URL.get(match.namespace, {}).get(match.url_name, "")
    counts = pending_counts()
    return {
        "active": active,
        "counts": counts,
        "hidden_count": hidden_count(),
        "total_pending": sum(counts.values()),
    }
