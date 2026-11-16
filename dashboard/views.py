import csv
from urllib.parse import urlencode

from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Min
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.models import User
from announcements.models import Announcement
from config.mixins import ContentStatus
from forum.models import ForumPost
from groups.models import Group
from interactions.models import Comment, Like
from moderation.models import AccountAppeal, ModerationLog, Report

from . import stats

TABS = {"overview": "Tổng quan", "moderation": "Kiểm duyệt"}


def _can_view_dashboard(user):
    # Chi Giao vu Khoa duoc xem thong ke dien dan (da chot voi nhom, khac voi de xuat
    # ban dau la ca BCN Khoa cung xem duoc)
    return user.is_authenticated and user.role == User.Role.STAFF


def _users_by_role():
    counts = dict(User.objects.values_list("role").annotate(count=Count("id")))
    return [{"label": label, "count": counts.get(value, 0)} for value, label in User.Role.choices]


# (khoa, nhan, queryset, truong thoi gian, so tong cong don) - thu tu = thu tu o so lieu tren dashboard
def _metric_sources():
    published_comments = Comment.objects.filter(status=Comment.Status.PUBLISHED)
    return [
        ("users", "Người dùng mới", User.objects.all(), "date_joined", User.objects.count()),
        ("posts", "Bài viết diễn đàn", ForumPost.objects.all(), "created_at",
         ForumPost.objects.filter(status=ContentStatus.PUBLISHED).count()),
        ("comments", "Bình luận", published_comments, "created_at", published_comments.count()),
        ("reactions", "Lượt cảm xúc", Like.objects.all(), "created_at", Like.objects.count()),
        ("announcements", "Thông báo", Announcement.objects.all(), "created_at",
         Announcement.objects.filter(status=ContentStatus.PUBLISHED).count()),
        ("groups", "Nhóm mới", Group.objects.all(), "created_at", Group.objects.filter(status=Group.Status.ACTIVE).count()),
    ]


# Bieu do hoat dong: 3 chuoi (gioi han 3 mau dau cua bang mau - xem dataviz: qua 3 chuoi khong con phan biet an toan)
CHART_SERIES = ("posts", "comments", "users")


def _year_options(today, viewing_year):
    """Cac nam co the chon: tu nam co du lieu som nhat (tai khoan / bai viet) - hoac nam dang xem neu som hon - den nam nay."""
    first_dates = [
        User.objects.aggregate(first=Min("date_joined"))["first"],
        ForumPost.objects.aggregate(first=Min("created_at"))["first"],
    ]
    years = [timezone.localtime(d).year for d in first_dates if d]
    return list(range(min(years + [today.year, viewing_year]), max(today.year, viewing_year) + 1))


def _current_params(info):
    """Tham so GET cua ky dang xem - dung cho link doi tab va nut xuat file (giu nguyen ky)."""
    if info["period"] == "week":
        return {"period": "week", "date": info["start"].isoformat()}
    if info["period"] == "month":
        return {"period": "month", "year": info["start"].year, "month": info["start"].month}
    return {"period": "year", "year": info["start"].year}


def _collect_metrics(info, series_keys=CHART_SERIES):
    metrics, series = [], {}
    for key, label, queryset, field, total in _metric_sources():
        current = stats.period_count(queryset, field, info["start"], info["end"])
        previous = stats.period_count(queryset, field, info["prev_start"], info["start"])
        metrics.append({
            "key": key, "label": label, "value": current, "previous": previous, "total": total,
            "delta": stats.delta_percent(current, previous),
        })
        if key in series_keys:
            series[key] = {"label": label, "values": stats.count_series(queryset, field, info)}
    return metrics, series


def _format_duration(hours):
    if hours is None:
        return "—"
    if hours < 1:
        return f"{round(hours * 60)} phút"
    if hours < 48:
        return f"{hours:.1f} giờ".replace(".", ",")
    return f"{hours / 24:.1f} ngày".replace(".", ",")


# Nhom hanh dong trong ModerationLog -> cot cua bang "Hoat dong xu ly" (D1)
_ACTIVITY_COLUMNS = [
    ("content", "Duyệt / từ chối nội dung", (ModerationLog.Action.APPROVE_CONTENT, ModerationLog.Action.REJECT_CONTENT)),
    ("hide_delete", "Ẩn / xóa nội dung", (ModerationLog.Action.HIDE, ModerationLog.Action.DELETE)),
    ("accounts", "Khóa / mở khóa tài khoản", (ModerationLog.Action.LOCK_ACCOUNT, ModerationLog.Action.UNLOCK_ACCOUNT)),
    ("appeals", "Xét kháng nghị", (ModerationLog.Action.APPEAL_ACCEPTED, ModerationLog.Action.APPEAL_REJECTED)),
]


def _moderation_stats(info):
    """D1 - so lieu kiem duyet cua ky dang xem: bao cao moi/da xu ly, thoi gian xu ly trung binh,
    noi dung bi bo loc giu lai, bao cao theo loai vi pham, hoat dong xu ly cua tung Giao vu."""
    start, end = stats._aware(info["start"]), stats._aware(info["end"])
    prev_start = stats._aware(info["prev_start"])

    reports = Report.objects.all()
    new_reports = reports.filter(created_at__gte=start, created_at__lt=end).count()
    prev_reports = reports.filter(created_at__gte=prev_start, created_at__lt=start).count()

    handled_qs = reports.filter(handled_at__gte=start, handled_at__lt=end)
    handled = handled_qs.count()
    dismissed = handled_qs.filter(status=Report.Status.DISMISSED).count()
    durations = [
        (h - c).total_seconds() / 3600 for c, h in handled_qs.values_list("created_at", "handled_at") if h and c
    ]
    avg_hours = sum(durations) / len(durations) if durations else None

    auto_held = ModerationLog.objects.filter(
        action=ModerationLog.Action.WARN, actor__isnull=True, created_at__gte=start, created_at__lt=end,
    ).count()

    by_category_raw = dict(
        reports.filter(created_at__gte=start, created_at__lt=end)
        .values_list("category").annotate(n=Count("id"))
    )
    peak = max(by_category_raw.values(), default=0)
    by_category = sorted(
        [
            {"label": label, "count": by_category_raw.get(value, 0),
             "pct": round(by_category_raw.get(value, 0) * 100 / peak) if peak else 0}
            for value, label in Report.Category.choices
        ],
        key=lambda row: -row["count"],
    )

    # Hoat dong tung Giao vu: bao cao da xu ly (Report.handled_by) + cac hanh dong trong ModerationLog
    activity = {}
    for user_id, n in handled_qs.values_list("handled_by").annotate(n=Count("id")):
        if user_id:
            activity.setdefault(user_id, {})["reports"] = n
    logs = (
        ModerationLog.objects.filter(actor__isnull=False, created_at__gte=start, created_at__lt=end)
        .values_list("actor", "action").annotate(n=Count("id"))
    )
    for user_id, action, n in logs:
        for key, _label, actions in _ACTIVITY_COLUMNS:
            if action in actions:
                row = activity.setdefault(user_id, {})
                row[key] = row.get(key, 0) + n
    actors = User.objects.in_bulk(list(activity.keys()))
    activity_rows = []
    for user_id, row in activity.items():
        cells = [row.get("reports", 0)] + [row.get(key, 0) for key, _l, _a in _ACTIVITY_COLUMNS]
        activity_rows.append({"actor": actors.get(user_id), "cells": cells, "total": sum(cells)})
    activity_rows.sort(key=lambda r: -r["total"])

    pending_content = sum(
        model.objects.filter(status=ContentStatus.PENDING_REVIEW).count() for model in (ForumPost, Announcement)
    ) + Comment.objects.filter(status=Comment.Status.PENDING_REVIEW).count()

    return {
        "tiles": [
            {"label": "Báo cáo mới", "value": new_reports, "previous": prev_reports,
             "delta": stats.delta_percent(new_reports, prev_reports)},
            {"label": "Báo cáo đã xử lý", "value": handled, "previous": None,
             "sub": f"Bỏ qua {dismissed}/{handled} ({round(dismissed * 100 / handled)}%)" if handled else "Chưa xử lý báo cáo nào"},
            {"label": "Thời gian xử lý trung bình", "value": _format_duration(avg_hours), "previous": None,
             "sub": "Từ lúc gửi báo cáo đến lúc xử lý xong"},
            {"label": "Nội dung bị bộ lọc giữ lại", "value": auto_held, "previous": None,
             "sub": "Trúng từ khóa nhạy cảm, chuyển chờ kiểm duyệt"},
        ],
        "new_reports": new_reports, "prev_reports": prev_reports, "handled": handled, "dismissed": dismissed,
        "avg_hours": avg_hours, "auto_held": auto_held,
        "by_category": by_category,
        "activity_headers": ["Báo cáo đã xử lý"] + [label for _k, label, _a in _ACTIVITY_COLUMNS],
        "activity_rows": activity_rows,
        "queue": {
            "reports": Report.objects.filter(status__in=Report.OPEN_STATUSES).count(),
            "content": pending_content,
            "appeals": AccountAppeal.objects.filter(status=AccountAppeal.Status.PENDING).count(),
        },
    }


@user_passes_test(_can_view_dashboard, login_url="home")
def overview(request):
    info = stats.resolve_period(request.GET.get("period"), request.GET)
    tab = request.GET.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    tab_suffix = "" if tab == "overview" else f"&tab={tab}"

    context = {
        "info": info,
        "tab": tab,
        "tabs": TABS,
        "tab_suffix": tab_suffix,
        "period": info["period"],
        "period_labels": stats.PERIOD_LABELS,
        "current_query": urlencode(_current_params(info)),
        "prev_query": urlencode(info["prev_params"]),
        "next_query": urlencode(info["next_params"]) if info["next_params"] else "",
        "year_options": _year_options(info["today"], info["start"].year),
        "month_options": range(1, 13),
    }
    if tab == "moderation":
        context["mod"] = _moderation_stats(info)
    else:
        metrics, series = _collect_metrics(info)
        chart = {
            "labels": [stats.bucket_label(b, info["unit"], info["period"]) for b in info["buckets"]],
            "series": [{"key": k, **series[k]} for k in CHART_SERIES],
        }
        context.update({
            "metrics": metrics,
            "chart": chart,
            "chart_rows": list(zip(chart["labels"], *[s["values"] for s in chart["series"]])),
            "users_by_role": _users_by_role(),
            "pending_groups": Group.objects.filter(status=Group.Status.PENDING).count(),
            "pending_reports": Report.objects.filter(status=Report.Status.PENDING).count(),
        })
    return render(request, "dashboard/overview.html", context)


@user_passes_test(_can_view_dashboard, login_url="home")
def export_csv(request):
    """D5 - xuat so lieu cua ky dang xem ra file mo bang Excel.

    Ghi UTF-16 (co BOM) + phan cach bang dau Tab: Excel mo dung tieng Viet va tach cot dung
    bat ke cai dat vung cua Windows (Excel ban tieng Viet mac dinh tach cot bang ';' nen file
    UTF-8 phan cach ',' bi don het vao 1 cot). Khong can cai them thu vien nhu openpyxl."""
    info = stats.resolve_period(request.GET.get("period"), request.GET)
    all_keys = [key for key, *_rest in _metric_sources()]
    metrics, series = _collect_metrics(info, series_keys=all_keys)
    mod = _moderation_stats(info)

    rows = [
        ["BÁO CÁO THỐNG KÊ EMT FORUM"],
        ["Kỳ thống kê", info["label"]],
        ["Xuất lúc", timezone.localtime().strftime("%d/%m/%Y %H:%M")],
        [],
        ["CHỈ SỐ", "Kỳ này", f"Kỳ trước ({info['prev_label']})", "Thay đổi (%)", "Tổng hiện có"],
    ]
    for m in metrics:
        rows.append([m["label"], m["value"], m["previous"], "" if m["delta"] is None else m["delta"], m["total"]])

    rows += [[], ["THEO THỜI GIAN", *[m["label"] for m in metrics]]]
    for i, bucket in enumerate(info["buckets"]):
        values = [series[m["key"]]["values"][i] for m in metrics]
        rows.append([stats.bucket_label(bucket, info["unit"], info["period"]),
                     *["" if v is None else v for v in values]])

    rows += [[], ["NGƯỜI DÙNG THEO VAI TRÒ", "Số tài khoản"]]
    rows += [[r["label"], r["count"]] for r in _users_by_role()]

    rows += [
        [], ["KIỂM DUYỆT", "Giá trị"],
        ["Báo cáo mới", mod["new_reports"]],
        ["Báo cáo đã xử lý", mod["handled"]],
        ["Trong đó bỏ qua (không vi phạm)", mod["dismissed"]],
        ["Thời gian xử lý trung bình (giờ)", "" if mod["avg_hours"] is None else round(mod["avg_hours"], 1)],
        ["Nội dung bị bộ lọc giữ lại", mod["auto_held"]],
        [], ["BÁO CÁO THEO LOẠI VI PHẠM", "Số báo cáo"],
    ]
    rows += [[row["label"], row["count"]] for row in mod["by_category"]]

    filename = f"emt-forum-thong-ke-{info['period']}-{info['start']:%Y-%m-%d}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-16")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    buffer = []

    class _Collector:
        def write(self, text):
            buffer.append(text)

    csv.writer(_Collector(), delimiter="\t", lineterminator="\r\n").writerows(rows)
    response.write("".join(buffer).encode("utf-16"))
    return response
